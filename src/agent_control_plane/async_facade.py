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
    AppEventMapper,
    KillResultDTO,
    SessionLifecycleResult,
    UnknownAppEventError,
)
from agent_control_plane.types.approvals import ApprovalTicketDTO
from agent_control_plane.types.enums import (
    AbortReason,
    ApprovalDecisionType,
    ApprovalStatus,
    EventKind,
    ExecutionMode,
    KillSwitchScope,
    ProposalStatus,
    SessionStatus,
    UnknownAppEventPolicy,
)
from agent_control_plane.types.frames import EventFrame
from agent_control_plane.types.ids import AgentId, IdempotencyKey
from agent_control_plane.types.proposals import ActionProposalDTO
from agent_control_plane.types.query import PageDTO, SessionHealthDTO, StateChangeDTO, StateChangePageDTO
from agent_control_plane.types.sessions import SessionState


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
            cached = await self._get_cached_command_result(uow, command_id, "open_session")
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
                "open_session",
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
    ) -> ApprovalTicketDTO:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            cached = await self._get_cached_command_result(uow, command_id, "create_ticket")
            if cached is not None:
                return ApprovalTicketDTO.model_validate(cached)
            ticket = await uow.approval_repo.create_ticket(session_id, proposal_id, timeout_at)
            await self._record_command_result(
                uow,
                command_id,
                "create_ticket",
                ticket.model_dump(mode="json"),
                session_id=session_id,
            )
            await uow.commit()
            return ticket

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
    ) -> ApprovalTicketDTO:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            cached = await self._get_cached_command_result(uow, command_id, "approve_ticket")
            if cached is not None:
                return ApprovalTicketDTO.model_validate(cached)
            ticket = await uow.approval_repo.get_pending_ticket_for_update(ticket_id)
            fields: dict[str, Any] = {
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
                "approve_ticket",
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
    ) -> ApprovalTicketDTO:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            cached = await self._get_cached_command_result(uow, command_id, "deny_ticket")
            if cached is not None:
                return ApprovalTicketDTO.model_validate(cached)
            ticket = await uow.approval_repo.get_pending_ticket_for_update(ticket_id)
            fields: dict[str, Any] = {
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
                "deny_ticket",
                result.model_dump(mode="json"),
                session_id=ticket.session_id,
            )
            await uow.commit()
            return result

    async def get_pending_tickets(self, session_id: UUID | None = None) -> list[ApprovalTicketDTO]:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            return await uow.approval_repo.get_pending_tickets(session_id)

    async def get_ticket(self, ticket_id: UUID) -> ApprovalTicketDTO | None:
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
    ) -> PageDTO[ApprovalTicketDTO]:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            rows = await uow.approval_repo.list_tickets(
                session_id=session_id,
                statuses=statuses,
                limit=limit + 1,
                offset=offset,
            )
            has_more = len(rows) > limit
            return PageDTO(items=rows[:limit], next_offset=(offset + limit if has_more else None))

    async def get_proposal(self, proposal_id: UUID) -> ActionProposalDTO | None:
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
    ) -> PageDTO[ActionProposalDTO]:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            rows = await uow.proposal_repo.list_proposals(
                session_id=session_id,
                statuses=statuses,
                limit=limit + 1,
                offset=offset,
            )
            has_more = len(rows) > limit
            return PageDTO(items=rows[:limit], next_offset=(offset + limit if has_more else None))

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
            cached = await self._get_cached_command_result(uow, command_id, "emit")
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
                "emit",
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
    ) -> StateChangePageDTO:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            rows = await uow.event_repo.list_state_bearing_events(
                session_id=session_id,
                offset=cursor,
                limit=limit + 1,
            )
            has_more = len(rows) > limit
            items = [StateChangeDTO(cursor=cursor + idx + 1, event=row) for idx, row in enumerate(rows[:limit])]
            return StateChangePageDTO(items=items, next_cursor=(cursor + limit if has_more else None))

    async def get_health_snapshot(self) -> SessionHealthDTO:
        created = await self.list_sessions(statuses=[SessionStatus.CREATED], limit=10_000)
        active = await self.list_sessions(statuses=[SessionStatus.ACTIVE], limit=10_000)
        paused = await self.list_sessions(statuses=[SessionStatus.PAUSED], limit=10_000)
        pending = await self.get_pending_tickets()
        sessions_with_cycles = sum(1 for session in created + active + paused if session.active_cycle_id is not None)
        return SessionHealthDTO(
            total_sessions=len(created) + len(active) + len(paused),
            active_sessions=len(active),
            created_sessions=len(created),
            paused_sessions=len(paused),
            sessions_with_active_cycles=sessions_with_cycles,
            pending_tickets=len(pending),
        )

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
            cached = await self._get_cached_command_result(uow, command_id, "close_session")
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
                "close_session",
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
            cached = await self._get_cached_command_result(uow, command_id, "abort_session")
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
                "abort_session",
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
    ) -> KillResultDTO:
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
    ) -> KillResultDTO:
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
    ) -> KillResultDTO:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            operation = f"kill:{scope.value}"
            cached = await self._get_cached_command_result(uow, command_id, operation)
            if cached is not None:
                return KillResultDTO.model_validate(cached)
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
                result = KillResultDTO(
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
                result = KillResultDTO(
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
