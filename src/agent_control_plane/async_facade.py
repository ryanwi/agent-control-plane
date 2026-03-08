"""First-class asynchronous facade for agent-control-plane."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from agent_control_plane.engine.concurrency import CycleAlreadyActiveError
from agent_control_plane.models.reference import Base, register_models
from agent_control_plane.models.registry import RegistryProtocol, ScopedModelRegistry, registry_scope
from agent_control_plane.storage.sqlalchemy_async import AsyncSqlAlchemyUnitOfWork
from agent_control_plane.sync import (
    CMD_ABORT_SESSION,
    CMD_APPROVE_TICKET,
    CMD_CLOSE_SESSION,
    CMD_CREATE_PROPOSAL,
    CMD_CREATE_TICKET,
    CMD_DENY_TICKET,
    CMD_EMIT,
    CMD_OPEN_SESSION,
    AppEventMapper,
    ApprovalTicketUpdateFields,
    KillResult,
    SessionLifecycleResult,
    UnknownAppEventError,
    guardrail_event_kind,
    kill_command_operation,
)
from agent_control_plane.types.agentic import (
    ControlPlaneScorecard,
    EvaluationResult,
    Goal,
    GuardrailDecision,
    HandoffResult,
    Plan,
    PlanProgress,
    PlanStep,
    RollbackResult,
    SessionCheckpoint,
)
from agent_control_plane.types.approvals import ApprovalTicket
from agent_control_plane.types.enums import (
    AbortReason,
    ApprovalDecisionType,
    ApprovalStatus,
    EvaluationDecision,
    EventKind,
    ExecutionMode,
    GoalStatus,
    GuardrailPhase,
    KillSwitchScope,
    PlanStepStatus,
    ProposalStatus,
    SessionStatus,
    UnknownAppEventPolicy,
)
from agent_control_plane.types.frames import EventFrame
from agent_control_plane.types.ids import AgentId, IdempotencyKey
from agent_control_plane.types.proposals import ActionProposal
from agent_control_plane.types.query import Page, SessionHealth, StateChange, StateChangePage
from agent_control_plane.types.sessions import SessionState


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, round((percentile / 100.0) * (len(ordered) - 1))))
    return ordered[idx]


def _normalize_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


class AsyncControlPlaneFacade:
    """Async control-plane facade for async host applications."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        engine: AsyncEngine | None = None,
        mapper: AppEventMapper | None = None,
        unknown_policy: UnknownAppEventPolicy = UnknownAppEventPolicy.RAISE,
        registry: RegistryProtocol | None = None,
        uow_factory: Callable[[AsyncSession], AsyncSqlAlchemyUnitOfWork] | None = None,
        register_reference_models: bool = True,
    ) -> None:
        self._engine = engine
        self._session_factory = session_factory
        self._mapper = mapper
        self._unknown_policy = unknown_policy
        self._registry = registry or ScopedModelRegistry()
        self._uow_factory = uow_factory or (lambda db: AsyncSqlAlchemyUnitOfWork(db))
        self._schema_lock = asyncio.Lock()
        self._schema_initialized = False
        if register_reference_models:
            register_models(registry=self._registry)

    @classmethod
    def from_database_url(
        cls,
        database_url: str,
        *,
        mapper: AppEventMapper | None = None,
        unknown_policy: UnknownAppEventPolicy = UnknownAppEventPolicy.RAISE,
        registry: RegistryProtocol | None = None,
        uow_factory: Callable[[AsyncSession], AsyncSqlAlchemyUnitOfWork] | None = None,
        register_reference_models: bool = True,
    ) -> AsyncControlPlaneFacade:
        engine = create_async_engine(database_url, future=True)
        session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
        return cls(
            session_factory=session_factory,
            engine=engine,
            mapper=mapper,
            unknown_policy=unknown_policy,
            registry=registry,
            uow_factory=uow_factory,
            register_reference_models=register_reference_models,
        )

    @classmethod
    def from_session_factory(
        cls,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        mapper: AppEventMapper | None = None,
        unknown_policy: UnknownAppEventPolicy = UnknownAppEventPolicy.RAISE,
        registry: RegistryProtocol | None = None,
        uow_factory: Callable[[AsyncSession], AsyncSqlAlchemyUnitOfWork] | None = None,
        register_reference_models: bool = True,
    ) -> AsyncControlPlaneFacade:
        return cls(
            session_factory=session_factory,
            mapper=mapper,
            unknown_policy=unknown_policy,
            registry=registry,
            uow_factory=uow_factory,
            register_reference_models=register_reference_models,
        )

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()

    async def _ensure_schema(self) -> None:
        if self._engine is None or self._schema_initialized:
            return
        async with self._schema_lock:
            if self._schema_initialized:
                return
            with registry_scope(self._registry):
                async with self._engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
            self._schema_initialized = True

    @asynccontextmanager
    async def session_scope(self) -> AsyncIterator[AsyncSession]:
        await self._ensure_schema()
        with registry_scope(self._registry):
            async with self._session_factory() as db:
                yield db

    async def open_session(
        self,
        name: str,
        *,
        max_cost: Decimal = Decimal("10000"),
        max_action_count: int = 50,
        execution_mode: ExecutionMode = ExecutionMode.DRY_RUN,
        command_id: IdempotencyKey | None = None,
    ) -> UUID:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            cached = await self._get_cached_command_result(uow, command_id, CMD_OPEN_SESSION)
            if cached is not None:
                raw_session_id = cached.get("session_id")
                if not isinstance(raw_session_id, str):
                    raise ValueError("Invalid cached idempotency payload for open_session")
                return UUID(raw_session_id)
            cs = await uow.session_repo.create_session(
                session_name=name,
                status=SessionStatus.CREATED,
                execution_mode=execution_mode,
                max_cost=max_cost,
                max_action_count=max_action_count,
            )
            await uow.session_repo.create_seq_counter(cs.id)
            await self._record_command_result(
                uow,
                command_id,
                CMD_OPEN_SESSION,
                {"session_id": str(cs.id)},
                session_id=cs.id,
            )
            await uow.commit()
            return cs.id

    async def get_session(self, session_id: UUID) -> SessionState | None:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            return await uow.session_repo.get_session(session_id)

    async def list_sessions(
        self,
        *,
        statuses: list[SessionStatus] | None = None,
        limit: int = 50,
    ) -> list[SessionState]:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            return await uow.session_repo.list_sessions(statuses=statuses, limit=limit)

    async def activate_session(self, session_id: UUID) -> SessionLifecycleResult:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            cs = await uow.session_repo.get_session_for_update(session_id)
            if cs.status != SessionStatus.CREATED:
                raise ValueError(f"Cannot activate session in state {cs.status}")
            await uow.session_repo.update_session(session_id, status=SessionStatus.ACTIVE, updated_at=datetime.now(UTC))
            await uow.commit()
            session = await uow.session_repo.get_session(session_id)
            if session is None:
                raise ValueError(f"Session not found after activation: {session_id}")
            return SessionLifecycleResult(session=session)

    async def pause_session(self, session_id: UUID) -> SessionLifecycleResult:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            cs = await uow.session_repo.get_session_for_update(session_id)
            if cs.status != SessionStatus.ACTIVE:
                raise ValueError(f"Cannot pause session in state {cs.status}")
            await uow.session_repo.update_session(session_id, status=SessionStatus.PAUSED, updated_at=datetime.now(UTC))
            await uow.commit()
            session = await uow.session_repo.get_session(session_id)
            if session is None:
                raise ValueError(f"Session not found after pause: {session_id}")
            return SessionLifecycleResult(session=session)

    async def resume_session(self, session_id: UUID) -> SessionLifecycleResult:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            cs = await uow.session_repo.get_session_for_update(session_id)
            if cs.status != SessionStatus.PAUSED:
                raise ValueError(f"Cannot resume session in state {cs.status}")
            await uow.session_repo.update_session(session_id, status=SessionStatus.ACTIVE, updated_at=datetime.now(UTC))
            await uow.commit()
            session = await uow.session_repo.get_session(session_id)
            if session is None:
                raise ValueError(f"Session not found after resume: {session_id}")
            return SessionLifecycleResult(session=session)

    async def set_active_cycle(self, session_id: UUID, cycle_id: UUID | None) -> None:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            await uow.session_repo.set_active_cycle(session_id, cycle_id)
            await uow.commit()

    async def acquire_cycle(self, session_id: UUID, cycle_id: UUID) -> None:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            cs = await uow.session_repo.get_session_for_update(session_id)
            if cs.active_cycle_id is not None:
                raise CycleAlreadyActiveError(f"Session {session_id} already has active cycle {cs.active_cycle_id}")
            await uow.session_repo.set_active_cycle(session_id, cycle_id)
            await uow.commit()

    async def release_cycle(self, session_id: UUID) -> None:
        await self.set_active_cycle(session_id, None)

    async def create_policy(self, **fields: Any) -> UUID:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            policy_id = await uow.session_repo.create_policy(**fields)
            await uow.commit()
            return policy_id

    async def create_ticket(
        self,
        session_id: UUID,
        proposal_id: UUID,
        timeout_at: datetime,
        *,
        command_id: IdempotencyKey | None = None,
    ) -> ApprovalTicket:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            cached = await self._get_cached_command_result(uow, command_id, CMD_CREATE_TICKET)
            if cached is not None:
                return ApprovalTicket.model_validate(cached)
            ticket = await uow.approval_repo.create_ticket(session_id, proposal_id, timeout_at)
            await self._record_command_result(
                uow,
                command_id,
                CMD_CREATE_TICKET,
                ticket.model_dump(mode="json"),
                session_id=session_id,
            )
            await uow.commit()
            return ticket

    async def create_proposal(
        self,
        proposal: ActionProposal,
        *,
        command_id: IdempotencyKey | None = None,
    ) -> ActionProposal:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            cached = await self._get_cached_command_result(uow, command_id, CMD_CREATE_PROPOSAL)
            if cached is not None:
                return ActionProposal.model_validate(cached)
            created = await uow.proposal_repo.create_proposal(proposal)
            await self._record_command_result(
                uow,
                command_id,
                CMD_CREATE_PROPOSAL,
                created.model_dump(mode="json"),
                session_id=proposal.session_id,
            )
            await uow.commit()
            return created

    async def approve_ticket(
        self,
        ticket_id: UUID,
        *,
        decided_by: str = "operator",
        reason: str | None = None,
        decision_type: ApprovalDecisionType = ApprovalDecisionType.ALLOW_ONCE,
        scope_resource_ids: list[str] | None = None,
        scope_max_cost: Decimal | None = None,
        scope_max_action_count: int | None = None,
        scope_expiry: datetime | None = None,
        command_id: IdempotencyKey | None = None,
    ) -> ApprovalTicket:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            cached = await self._get_cached_command_result(uow, command_id, CMD_APPROVE_TICKET)
            if cached is not None:
                return ApprovalTicket.model_validate(cached)
            ticket = await uow.approval_repo.get_pending_ticket_for_update(ticket_id)
            fields: ApprovalTicketUpdateFields = {
                "status": ApprovalStatus.APPROVED,
                "decision_type": decision_type,
                "decided_by": decided_by,
                "decision_reason": reason,
                "decided_at": datetime.now(UTC),
            }
            if decision_type == ApprovalDecisionType.ALLOW_FOR_SESSION:
                fields["scope_resource_ids"] = scope_resource_ids
                fields["scope_max_cost"] = scope_max_cost
                fields["scope_max_count"] = scope_max_action_count
                fields["scope_expiry"] = scope_expiry
            await uow.approval_repo.update_ticket(ticket_id, **fields)
            await uow.proposal_repo.update_status(ticket.proposal_id, ProposalStatus.APPROVED)
            result = ticket.model_copy(update=fields)
            await self._record_command_result(
                uow,
                command_id,
                CMD_APPROVE_TICKET,
                result.model_dump(mode="json"),
                session_id=ticket.session_id,
            )
            await uow.commit()
            return result

    async def deny_ticket(
        self,
        ticket_id: UUID,
        *,
        reason: str = "",
        command_id: IdempotencyKey | None = None,
    ) -> ApprovalTicket:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            cached = await self._get_cached_command_result(uow, command_id, CMD_DENY_TICKET)
            if cached is not None:
                return ApprovalTicket.model_validate(cached)
            ticket = await uow.approval_repo.get_pending_ticket_for_update(ticket_id)
            fields: ApprovalTicketUpdateFields = {
                "status": ApprovalStatus.DENIED,
                "decision_reason": reason,
                "decided_at": datetime.now(UTC),
            }
            await uow.approval_repo.update_ticket(ticket_id, **fields)
            await uow.proposal_repo.update_status(ticket.proposal_id, ProposalStatus.DENIED)
            result = ticket.model_copy(update=fields)
            await self._record_command_result(
                uow,
                command_id,
                CMD_DENY_TICKET,
                result.model_dump(mode="json"),
                session_id=ticket.session_id,
            )
            await uow.commit()
            return result

    async def get_pending_tickets(self, session_id: UUID | None = None) -> list[ApprovalTicket]:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            return await uow.approval_repo.get_pending_tickets(session_id)

    async def get_ticket(self, ticket_id: UUID) -> ApprovalTicket | None:
        """Return a single approval ticket by ID, or None if not found."""
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            return await uow.approval_repo.get_ticket(ticket_id)

    async def list_tickets(
        self,
        *,
        session_id: UUID | None = None,
        statuses: list[ApprovalStatus] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[ApprovalTicket]:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            rows = await uow.approval_repo.list_tickets(
                session_id=session_id,
                statuses=statuses,
                limit=limit + 1,
                offset=offset,
            )
            has_more = len(rows) > limit
            return Page(items=rows[:limit], next_offset=(offset + limit if has_more else None))

    async def get_proposal(self, proposal_id: UUID) -> ActionProposal | None:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            return await uow.proposal_repo.get_proposal(proposal_id)

    async def list_proposals(
        self,
        *,
        session_id: UUID | None = None,
        statuses: list[ProposalStatus] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[ActionProposal]:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            rows = await uow.proposal_repo.list_proposals(
                session_id=session_id,
                statuses=statuses,
                limit=limit + 1,
                offset=offset,
            )
            has_more = len(rows) > limit
            return Page(items=rows[:limit], next_offset=(offset + limit if has_more else None))

    async def expire_timed_out_tickets(self) -> int:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            expired = await uow.approval_repo.expire_timed_out()
            for ticket in expired:
                await uow.event_repo.append(
                    ticket.session_id,
                    EventKind.APPROVAL_TIMEOUT,
                    {"ticket_id": str(ticket.id), "proposal_id": str(ticket.proposal_id)},
                    state_bearing=False,
                )
            await uow.commit()
            return len(expired)

    async def check_budget(self, session_id: UUID, cost: Decimal = Decimal("0"), action_count: int = 1) -> bool:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            info = await uow.session_repo.get_budget(session_id)
            return cost <= info.remaining_cost and action_count <= info.remaining_count

    async def increment_budget(self, session_id: UUID, cost: Decimal, action_count: int = 1) -> None:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            await uow.session_repo.increment_budget(session_id, cost, action_count)
            await uow.commit()

    async def get_remaining_budget(self, session_id: UUID) -> dict[str, Decimal | int]:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            info = await uow.session_repo.get_budget(session_id)
            return {
                "remaining_cost": info.remaining_cost,
                "remaining_count": info.remaining_count,
                "used_cost": info.used_cost,
                "used_count": info.used_count,
                "max_cost": info.max_cost,
                "max_count": info.max_count,
            }

    async def emit(
        self,
        session_id: UUID,
        event_kind: EventKind,
        payload: dict[str, object],
        *,
        state_bearing: bool = False,
        agent_id: AgentId | None = None,
        correlation_id: UUID | None = None,
        routing_decision: dict[str, object] | None = None,
        routing_reason: str | None = None,
        idempotency_key: IdempotencyKey | None = None,
        command_id: IdempotencyKey | None = None,
    ) -> int:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            cached = await self._get_cached_command_result(uow, command_id, CMD_EMIT)
            if cached is not None:
                seq = cached.get("seq")
                if not isinstance(seq, int):
                    raise ValueError("Invalid cached idempotency payload for emit")
                return seq
            seq = await uow.event_repo.append(
                session_id=session_id,
                event_kind=event_kind,
                payload=dict(payload),
                state_bearing=state_bearing,
                agent_id=agent_id,
                correlation_id=correlation_id,
                routing_decision=dict(routing_decision) if routing_decision else None,
                routing_reason=routing_reason,
                idempotency_key=idempotency_key,
            )
            await self._record_command_result(
                uow,
                command_id,
                CMD_EMIT,
                {"seq": seq},
                session_id=session_id,
            )
            await uow.commit()
            return seq

    async def emit_app(
        self,
        session_id: UUID,
        event_name: str,
        payload: Mapping[str, object],
        *,
        state_bearing: bool | None = None,
        agent_id: AgentId | None = None,
        correlation_id: UUID | None = None,
        idempotency_key: IdempotencyKey | None = None,
    ) -> int | None:
        if self._mapper is None:
            raise ValueError("No app event mapper configured")
        mapped = self._mapper.map_event(event_name, payload)
        if mapped is None:
            if self._unknown_policy == UnknownAppEventPolicy.IGNORE:
                return None
            raise UnknownAppEventError(f"Unknown app event: {event_name}")
        return await self.emit(
            session_id=session_id,
            event_kind=mapped.event_kind,
            payload=mapped.payload,
            state_bearing=mapped.state_bearing if state_bearing is None else state_bearing,
            agent_id=mapped.agent_id if agent_id is None else agent_id,
            correlation_id=mapped.correlation_id if correlation_id is None else correlation_id,
            routing_decision=mapped.routing_decision,
            routing_reason=mapped.routing_reason,
            idempotency_key=mapped.idempotency_key if idempotency_key is None else idempotency_key,
        )

    async def replay(self, session_id: UUID, *, after_seq: int = 0, limit: int = 100) -> list[EventFrame]:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            return await uow.event_repo.replay(session_id, after_seq=after_seq, limit=limit)

    async def get_state_change_feed(
        self,
        *,
        session_id: UUID | None = None,
        cursor: int = 0,
        limit: int = 100,
    ) -> StateChangePage:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            rows = await uow.event_repo.list_state_bearing_events(
                session_id=session_id,
                offset=cursor,
                limit=limit + 1,
            )
            has_more = len(rows) > limit
            items = [StateChange(cursor=cursor + idx + 1, event=row) for idx, row in enumerate(rows[:limit])]
            return StateChangePage(items=items, next_cursor=(cursor + limit if has_more else None))

    async def get_health_snapshot(self) -> SessionHealth:
        created = await self.list_sessions(statuses=[SessionStatus.CREATED], limit=10_000)
        active = await self.list_sessions(statuses=[SessionStatus.ACTIVE], limit=10_000)
        paused = await self.list_sessions(statuses=[SessionStatus.PAUSED], limit=10_000)
        pending = await self.get_pending_tickets()
        sessions_with_cycles = sum(1 for session in created + active + paused if session.active_cycle_id is not None)
        return SessionHealth(
            total_sessions=len(created) + len(active) + len(paused),
            active_sessions=len(active),
            created_sessions=len(created),
            paused_sessions=len(paused),
            sessions_with_active_cycles=sessions_with_cycles,
            pending_tickets=len(pending),
        )

    async def create_checkpoint(
        self,
        session_id: UUID,
        *,
        label: str,
        metadata: dict[str, object] | None = None,
        created_by: str = "system",
        command_id: IdempotencyKey | None = None,
    ) -> SessionCheckpoint:
        operation = "checkpoint:create"
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            cached = await self._get_cached_command_result(uow, command_id, operation)
            if cached is not None:
                return SessionCheckpoint.model_validate(cached)
            last = await uow.event_repo.get_last_event(session_id)
            cp = SessionCheckpoint(
                session_id=session_id,
                event_seq=last.seq if last is not None else 0,
                label=label,
                metadata=dict(metadata or {}),
                created_by=created_by,
            )
            await uow.event_repo.append(
                session_id,
                EventKind.CHECKPOINT_CREATED,
                cp.model_dump(mode="json"),
                state_bearing=True,
            )
            await self._record_command_result(
                uow,
                command_id,
                operation,
                cp.model_dump(mode="json"),
                session_id=session_id,
            )
            await uow.commit()
            return cp

    async def list_checkpoints(self, session_id: UUID, *, limit: int = 50, offset: int = 0) -> Page[SessionCheckpoint]:
        rows = await self.replay(session_id, after_seq=0, limit=10_000)
        checkpoints = [
            SessionCheckpoint.model_validate(e.payload)
            for e in rows
            if e.event_kind == EventKind.CHECKPOINT_CREATED and isinstance(e.payload, dict)
        ]
        sliced = checkpoints[offset : offset + limit + 1]
        has_more = len(sliced) > limit
        return Page(items=sliced[:limit], next_offset=(offset + limit if has_more else None))

    async def rollback_to_checkpoint(
        self,
        session_id: UUID,
        checkpoint_id: UUID,
        *,
        reason: str,
        command_id: IdempotencyKey | None = None,
    ) -> RollbackResult:
        operation = "checkpoint:rollback"
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            cached = await self._get_cached_command_result(uow, command_id, operation)
            if cached is not None:
                return RollbackResult.model_validate(cached)
            cps = (await self.list_checkpoints(session_id, limit=10_000, offset=0)).items
            target = next((cp for cp in cps if cp.id == checkpoint_id), None)
            if target is None:
                raise ValueError(f"Checkpoint not found: {checkpoint_id}")
            last = await uow.event_repo.get_last_event(session_id)
            from_seq = last.seq if last is not None else 0
            await uow.event_repo.append(
                session_id,
                EventKind.ROLLBACK_REQUESTED,
                {"checkpoint_id": str(checkpoint_id), "reason": reason},
                state_bearing=True,
            )
            result = RollbackResult(
                session_id=session_id,
                from_seq=from_seq,
                to_seq=target.event_seq,
                restored_fields=["session_state", "proposal_state", "approval_state"],
                events_appended=2,
            )
            await uow.event_repo.append(
                session_id,
                EventKind.ROLLBACK_COMPLETED,
                result.model_dump(mode="json"),
                state_bearing=True,
            )
            await self._record_command_result(
                uow,
                command_id,
                operation,
                result.model_dump(mode="json"),
                session_id=session_id,
            )
            await uow.commit()
            return result

    async def create_goal(
        self,
        session_id: UUID,
        *,
        name: str,
        description: str = "",
        metadata: dict[str, object] | None = None,
    ) -> Goal:
        goal = Goal(
            session_id=session_id,
            name=name,
            description=description,
            status=GoalStatus.ACTIVE,
            metadata=dict(metadata or {}),
        )
        await self.emit(session_id, EventKind.GOAL_CREATED, goal.model_dump(mode="json"), state_bearing=True)
        return goal

    async def create_plan(self, session_id: UUID, goal_id: UUID, *, title: str, steps: list[str]) -> Plan:
        plan_steps = [PlanStep(plan_id=UUID(int=0), step_index=i, title=step) for i, step in enumerate(steps)]
        plan = Plan(session_id=session_id, goal_id=goal_id, title=title, steps=plan_steps)
        plan.steps = [step.model_copy(update={"plan_id": plan.id}) for step in plan.steps]
        await self.emit(session_id, EventKind.PLAN_CREATED, plan.model_dump(mode="json"), state_bearing=True)
        return plan

    async def start_plan_step(self, session_id: UUID, plan_id: UUID, *, step_index: int) -> PlanStep:
        step = PlanStep(
            plan_id=plan_id,
            step_index=step_index,
            title=f"step-{step_index}",
            status=PlanStepStatus.RUNNING,
        )
        await self.emit(session_id, EventKind.PLAN_STEP_STARTED, step.model_dump(mode="json"), state_bearing=True)
        return step

    async def complete_plan_step(
        self, session_id: UUID, plan_id: UUID, *, step_index: int, notes: str | None = None
    ) -> PlanStep:
        step = PlanStep(
            plan_id=plan_id,
            step_index=step_index,
            title=f"step-{step_index}",
            status=PlanStepStatus.SUCCEEDED,
            notes=notes,
        )
        await self.emit(session_id, EventKind.PLAN_STEP_COMPLETED, step.model_dump(mode="json"), state_bearing=True)
        return step

    async def get_plan_progress(self, session_id: UUID, goal_id: UUID) -> PlanProgress:
        events = await self.replay(session_id, after_seq=0, limit=10_000)
        goal = next(
            (
                Goal.model_validate(e.payload)
                for e in events
                if (
                    e.event_kind == EventKind.GOAL_CREATED
                    and isinstance(e.payload, dict)
                    and e.payload.get("id") == str(goal_id)
                )
            ),
            None,
        )
        if goal is None:
            raise ValueError(f"Goal not found: {goal_id}")
        plan = next(
            (
                Plan.model_validate(e.payload)
                for e in events
                if (
                    e.event_kind == EventKind.PLAN_CREATED
                    and isinstance(e.payload, dict)
                    and e.payload.get("goal_id") == str(goal_id)
                )
            ),
            None,
        )
        completed_steps = 0
        failed_steps = 0
        running_steps = 0
        if plan is not None:
            total_steps = len(plan.steps)
            for e in events:
                if not isinstance(e.payload, dict) or e.payload.get("plan_id") != str(plan.id):
                    continue
                if e.event_kind == EventKind.PLAN_STEP_COMPLETED:
                    completed_steps += 1
                elif e.event_kind == EventKind.PLAN_STEP_FAILED:
                    failed_steps += 1
                elif e.event_kind == EventKind.PLAN_STEP_STARTED:
                    running_steps += 1
        else:
            total_steps = 0
        return PlanProgress(
            goal=goal,
            plan=plan,
            total_steps=total_steps,
            completed_steps=completed_steps,
            failed_steps=failed_steps,
            running_steps=running_steps,
        )

    async def record_evaluation(
        self,
        session_id: UUID,
        *,
        operation: str,
        decision: EvaluationDecision,
        score: float,
        reasons: list[str],
        actions: list[str] | None = None,
    ) -> EvaluationResult:
        result = EvaluationResult(
            session_id=session_id,
            operation=operation,
            decision=decision,
            score=score,
            reasons=reasons,
            actions=list(actions or []),
        )
        event_kind = (
            EventKind.EVALUATION_PASSED if decision == EvaluationDecision.PASS else EventKind.EVALUATION_BLOCKED
        )
        await self.emit(session_id, event_kind, result.model_dump(mode="json"), state_bearing=False)
        return result

    async def apply_guardrail(
        self,
        session_id: UUID,
        *,
        phase: GuardrailPhase,
        allow: bool,
        policy_code: str,
        reason: str,
        metadata: dict[str, object] | None = None,
    ) -> GuardrailDecision:
        result = GuardrailDecision(
            session_id=session_id,
            phase=phase,
            allow=allow,
            policy_code=policy_code,
            reason=reason,
            metadata=dict(metadata or {}),
        )
        await self.emit(
            session_id,
            guardrail_event_kind(phase),
            result.model_dump(mode="json"),
            state_bearing=False,
        )
        return result

    async def request_handoff(
        self,
        session_id: UUID,
        *,
        source_agent_id: str,
        target_agent_id: str,
        allowed_actions: list[str],
        accepted: bool = True,
        lease_seconds: int = 900,
        metadata: dict[str, object] | None = None,
    ) -> HandoffResult:
        expires_at = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        result = HandoffResult(
            session_id=session_id,
            source_agent_id=source_agent_id,
            target_agent_id=target_agent_id,
            allowed_actions=allowed_actions,
            accepted=accepted,
            lease_expires_at=expires_at,
            metadata=dict(metadata or {}),
        )
        event_kind = EventKind.HANDOFF_ACCEPTED if accepted else EventKind.HANDOFF_REJECTED
        await self.emit(session_id, event_kind, result.model_dump(mode="json"), state_bearing=False)
        return result

    async def get_operational_scorecard(  # noqa: C901
        self,
        *,
        session_id: UUID | None = None,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> ControlPlaneScorecard:
        sessions = [session_id] if session_id is not None else [s.id for s in await self.list_sessions(limit=10_000)]
        scorecard = ControlPlaneScorecard()
        normalized_window_start = _normalize_utc(window_start) if window_start is not None else None
        normalized_window_end = _normalize_utc(window_end) if window_end is not None else None
        approval_latencies: list[float] = []
        rollback_latencies: list[float] = []
        total_cost = 0.0
        successful_actions = 0
        for sid in sessions:
            events = await self.replay(sid, after_seq=0, limit=10_000)
            pending_checkpoint_at: datetime | None = None
            approval_requested_at: datetime | None = None
            for event in events:
                event_created_at = _normalize_utc(event.created_at)
                if normalized_window_start and event_created_at < normalized_window_start:
                    continue
                if normalized_window_end and event_created_at > normalized_window_end:
                    continue
                scorecard.total_events += 1
                if event.event_kind == EventKind.CHECKPOINT_CREATED:
                    scorecard.checkpoints_created += 1
                    pending_checkpoint_at = event_created_at
                elif event.event_kind == EventKind.ROLLBACK_COMPLETED:
                    scorecard.rollbacks_completed += 1
                    if pending_checkpoint_at is not None:
                        rollback_latencies.append((event_created_at - pending_checkpoint_at).total_seconds() * 1000.0)
                elif event.event_kind == EventKind.EVALUATION_BLOCKED:
                    scorecard.evaluations_blocked += 1
                    if isinstance(event.payload, dict):
                        for reason in event.payload.get("reasons", []):
                            key = str(reason)
                            scorecard.evaluation_block_reasons[key] = scorecard.evaluation_block_reasons.get(key, 0) + 1
                elif event.event_kind in (
                    EventKind.GUARDRAIL_INPUT,
                    EventKind.GUARDRAIL_TOOL,
                    EventKind.GUARDRAIL_OUTPUT,
                ):
                    if isinstance(event.payload, dict):
                        code = str(event.payload.get("policy_code", "unknown"))
                        scorecard.guardrail_policy_code_counts[code] = (
                            scorecard.guardrail_policy_code_counts.get(code, 0) + 1
                        )
                        if event.payload.get("allow") is False:
                            scorecard.guardrail_denies += 1
                        else:
                            scorecard.guardrail_allows += 1
                elif event.event_kind == EventKind.HANDOFF_ACCEPTED:
                    scorecard.handoffs_accepted += 1
                elif event.event_kind == EventKind.HANDOFF_REJECTED:
                    scorecard.handoffs_rejected += 1
                elif event.event_kind == EventKind.APPROVAL_REQUESTED:
                    approval_requested_at = event_created_at
                elif event.event_kind in (EventKind.APPROVAL_GRANTED, EventKind.APPROVAL_DENIED):
                    if approval_requested_at is not None:
                        approval_latencies.append((event_created_at - approval_requested_at).total_seconds() * 1000.0)
                        approval_requested_at = None
                elif event.event_kind == EventKind.BUDGET_EXHAUSTED:
                    scorecard.budget_exhausted_count += 1
                elif event.event_kind == EventKind.EXECUTION_COMPLETED:
                    successful_actions += 1
                    if isinstance(event.payload, dict):
                        value = event.payload.get("cost")
                        if isinstance(value, int | float):
                            total_cost += float(value)
            scorecard.budget_denied_count += sum(
                1
                for e in events
                if e.event_kind == EventKind.KILL_SWITCH_TRIGGERED
                and isinstance(e.payload, dict)
                and e.payload.get("reason") in ("budget_denied", "budget_exhausted")
            )
        scorecard.approval_latency_ms_p50 = _percentile(approval_latencies, 50)
        scorecard.approval_latency_ms_p95 = _percentile(approval_latencies, 95)
        scorecard.checkpoint_rollback_latency_ms_p50 = _percentile(rollback_latencies, 50)
        scorecard.checkpoint_rollback_latency_ms_p95 = _percentile(rollback_latencies, 95)
        scorecard.avg_cost_per_successful_action = (total_cost / successful_actions) if successful_actions > 0 else None
        handoff_total = scorecard.handoffs_accepted + scorecard.handoffs_rejected
        scorecard.handoff_accept_rate = (scorecard.handoffs_accepted / handoff_total) if handoff_total > 0 else None
        return scorecard

    async def close_session(
        self,
        session_id: UUID,
        *,
        final_event_kind: EventKind | None = None,
        payload: dict[str, object] | None = None,
        command_id: IdempotencyKey | None = None,
    ) -> SessionLifecycleResult:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            cached = await self._get_cached_command_result(uow, command_id, CMD_CLOSE_SESSION)
            if cached is not None:
                return SessionLifecycleResult.model_validate(cached)
            appended = 0
            if final_event_kind is not None:
                await uow.event_repo.append(
                    session_id=session_id,
                    event_kind=final_event_kind,
                    payload=dict(payload or {}),
                    state_bearing=True,
                )
                appended = 1
            await uow.session_repo.update_session(
                session_id,
                status=SessionStatus.COMPLETED,
                active_cycle_id=None,
                updated_at=datetime.now(UTC),
            )
            await uow.commit()
            session = await uow.session_repo.get_session(session_id)
            if session is None:
                raise ValueError(f"Session not found after completion: {session_id}")
            result = SessionLifecycleResult(session=session, events_appended=appended)
            await self._record_command_result(
                uow,
                command_id,
                CMD_CLOSE_SESSION,
                result.model_dump(mode="json"),
                session_id=session_id,
            )
            return result

    async def abort_session(
        self,
        session_id: UUID,
        *,
        reason: str = "Session aborted",
        abort_reason: AbortReason = AbortReason.OPERATOR_REQUEST,
        command_id: IdempotencyKey | None = None,
    ) -> SessionLifecycleResult:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            cached = await self._get_cached_command_result(uow, command_id, CMD_ABORT_SESSION)
            if cached is not None:
                return SessionLifecycleResult.model_validate(cached)
            await uow.session_repo.update_session(
                session_id,
                status=SessionStatus.ABORTED,
                abort_reason=abort_reason,
                abort_details=reason,
                active_cycle_id=None,
                updated_at=datetime.now(UTC),
            )
            await uow.commit()
            session = await uow.session_repo.get_session(session_id)
            if session is None:
                raise ValueError(f"Session not found after abort: {session_id}")
            result = SessionLifecycleResult(session=session)
            await self._record_command_result(
                uow,
                command_id,
                CMD_ABORT_SESSION,
                result.model_dump(mode="json"),
                session_id=session_id,
            )
            return result

    async def kill_session(
        self,
        session_id: UUID,
        *,
        reason: str = "Kill switch triggered",
        command_id: IdempotencyKey | None = None,
    ) -> KillResult:
        return await self._trigger_kill(
            KillSwitchScope.SESSION_ABORT,
            session_id=session_id,
            reason=reason,
            command_id=command_id,
        )

    async def kill_system(
        self,
        *,
        reason: str = "System halt",
        command_id: IdempotencyKey | None = None,
    ) -> KillResult:
        return await self._trigger_kill(KillSwitchScope.SYSTEM_HALT, reason=reason, command_id=command_id)

    async def recover_stuck_sessions(self) -> dict[str, int]:
        sessions = await self.list_sessions(statuses=[SessionStatus.ACTIVE], limit=1000)
        stuck_sessions = 0
        recovered = 0
        aborted = 0

        for cs in sessions:
            if cs.active_cycle_id is None:
                continue
            stuck_sessions += 1
            async with self.session_scope() as db:
                uow = self._uow_factory(db)
                try:
                    await uow.session_repo.update_session(
                        cs.id,
                        active_cycle_id=None,
                        updated_at=datetime.now(UTC),
                    )
                    await uow.event_repo.append(
                        cs.id,
                        EventKind.CYCLE_RECOVERED,
                        {"previous_cycle_id": str(cs.active_cycle_id)},
                        state_bearing=True,
                    )
                    recovered += 1
                except Exception:
                    await uow.session_repo.update_session(
                        cs.id,
                        status=SessionStatus.ABORTED,
                        abort_reason=AbortReason.SYSTEM_ERROR,
                        abort_details="Failed to recover stuck cycle safely",
                        active_cycle_id=None,
                        updated_at=datetime.now(UTC),
                    )
                    await uow.event_repo.append(
                        cs.id,
                        EventKind.SESSION_ABORTED,
                        {"reason": "stuck_cycle_recovery_failed"},
                        state_bearing=True,
                    )
                    aborted += 1
                await uow.commit()

        return {"stuck_sessions": stuck_sessions, "recovered": recovered, "aborted": aborted}

    async def check_stuck_cycles(self, timeout_seconds: int = 900) -> dict[str, int]:
        sessions = await self.list_sessions(statuses=[SessionStatus.ACTIVE], limit=1000)
        checked = 0
        escalated = 0
        cutoff = datetime.now(UTC) - timedelta(seconds=timeout_seconds)

        for cs in sessions:
            if cs.active_cycle_id is None:
                continue
            checked += 1
            async with self.session_scope() as db:
                uow = self._uow_factory(db)
                last_event = await uow.event_repo.get_last_event(cs.id)
                if last_event is None or last_event.created_at < cutoff:
                    await uow.session_repo.update_session(
                        cs.id,
                        active_cycle_id=None,
                        updated_at=datetime.now(UTC),
                    )
                    await uow.event_repo.append(
                        cs.id,
                        EventKind.KILL_SWITCH_TRIGGERED,
                        {
                            "scope": "cycle_timeout",
                            "timeout_seconds": timeout_seconds,
                        },
                        state_bearing=True,
                    )
                    escalated += 1
                await uow.commit()

        return {"checked": checked, "escalated": escalated}

    async def _trigger_kill(
        self,
        scope: KillSwitchScope,
        *,
        session_id: UUID | None = None,
        reason: str = "Kill switch triggered",
        command_id: IdempotencyKey | None = None,
    ) -> KillResult:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            operation = kill_command_operation(scope)
            cached = await self._get_cached_command_result(uow, command_id, operation)
            if cached is not None:
                return KillResult.model_validate(cached)
            if scope == KillSwitchScope.SESSION_ABORT:
                if session_id is None:
                    raise ValueError("session_id required for session_abort")
                await uow.session_repo.update_session(
                    session_id,
                    status=SessionStatus.ABORTED,
                    abort_reason=AbortReason.KILL_SWITCH,
                    abort_details=reason,
                    active_cycle_id=None,
                    updated_at=datetime.now(UTC),
                )
                denied = await uow.approval_repo.deny_all_pending(session_id)
                await uow.event_repo.append(
                    session_id,
                    EventKind.SESSION_ABORTED,
                    {"reason": reason, "tickets_denied": denied},
                    state_bearing=True,
                )
                result = KillResult(
                    scope=KillSwitchScope.SESSION_ABORT,
                    session_id=session_id,
                    tickets_denied=denied,
                )
                await self._record_command_result(
                    uow,
                    command_id,
                    operation,
                    result.model_dump(mode="json"),
                    session_id=session_id,
                )
                await uow.commit()
                return result

            if scope == KillSwitchScope.SYSTEM_HALT:
                sessions = await uow.session_repo.list_sessions(statuses=[SessionStatus.ACTIVE, SessionStatus.CREATED])
                denied_total = 0
                for cs in sessions:
                    await uow.session_repo.update_session(
                        cs.id,
                        status=SessionStatus.ABORTED,
                        abort_reason=AbortReason.KILL_SWITCH,
                        abort_details=reason,
                        active_cycle_id=None,
                        updated_at=datetime.now(UTC),
                    )
                    denied_total += await uow.approval_repo.deny_all_pending(cs.id)
                    await uow.event_repo.append(
                        cs.id,
                        EventKind.KILL_SWITCH_TRIGGERED,
                        {"scope": "system_halt", "reason": reason},
                        state_bearing=True,
                    )
                result = KillResult(
                    scope=KillSwitchScope.SYSTEM_HALT,
                    sessions_aborted=len(sessions),
                    tickets_denied=denied_total,
                )
                await self._record_command_result(
                    uow,
                    command_id,
                    operation,
                    result.model_dump(mode="json"),
                )
                await uow.commit()
                return result

            raise ValueError(f"Unsupported kill scope for async API: {scope}")

    async def _get_cached_command_result(
        self,
        uow: AsyncSqlAlchemyUnitOfWork,
        command_id: IdempotencyKey | None,
        operation: str,
    ) -> dict[str, object] | None:
        if command_id is None:
            return None
        cached = await uow.command_repo.get_command(str(command_id))
        if cached is None:
            return None
        if cached.operation != operation:
            raise ValueError(f"Command id {command_id} already used for operation {cached.operation}")
        return cached.result

    async def _record_command_result(
        self,
        uow: AsyncSqlAlchemyUnitOfWork,
        command_id: IdempotencyKey | None,
        operation: str,
        result: dict[str, object],
        *,
        session_id: UUID | None = None,
    ) -> None:
        if command_id is None:
            return
        await uow.command_repo.record_command(
            str(command_id),
            operation,
            result,
            session_id=session_id,
        )
