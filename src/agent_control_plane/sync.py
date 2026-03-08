"""First-class synchronous API for agent-control-plane."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final, Protocol, TypedDict, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from agent_control_plane.models.reference import Base, register_models
from agent_control_plane.models.registry import (
    RegistryProtocol,
    ScopedModelRegistry,
    registry_scope,
)
from agent_control_plane.storage.sqlalchemy_sync import SyncSqlAlchemyUnitOfWork
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

CMD_OPEN_SESSION: Final[str] = "open_session"
CMD_CLOSE_SESSION: Final[str] = "close_session"
CMD_ABORT_SESSION: Final[str] = "abort_session"
CMD_EMIT: Final[str] = "emit"
CMD_CREATE_TICKET: Final[str] = "create_ticket"
CMD_APPROVE_TICKET: Final[str] = "approve_ticket"
CMD_DENY_TICKET: Final[str] = "deny_ticket"


def kill_command_operation(scope: KillSwitchScope) -> str:
    return f"kill:{scope.value}"


class ApprovalTicketUpdateFields(TypedDict, total=False):
    status: ApprovalStatus
    decision_type: ApprovalDecisionType
    decided_by: str
    decision_reason: str | None
    decided_at: datetime
    scope_resource_ids: list[str] | None
    scope_max_cost: Decimal | None
    scope_max_count: int | None
    scope_expiry: datetime | None


class KillResultDTO(BaseModel):
    scope: KillSwitchScope
    session_id: UUID | None = None
    agent_id: AgentId | None = None
    sessions_aborted: int | None = None
    sessions_affected: int | None = None
    tickets_denied: int = 0


class SessionLifecycleResult(BaseModel):
    """Lifecycle operation result with updated session state."""

    session: SessionState
    events_appended: int = 0


class MappedEventDTO(BaseModel):
    """Resolved control-plane event details produced by an app-event mapper."""

    event_kind: EventKind
    payload: dict[str, Any] = Field(default_factory=dict)
    state_bearing: bool = False
    agent_id: AgentId | None = None
    correlation_id: UUID | None = None
    routing_decision: dict[str, Any] | None = None
    routing_reason: str | None = None
    idempotency_key: IdempotencyKey | None = None


@runtime_checkable
class AppEventMapper(Protocol):
    """Boundary adapter for host-app event names to control-plane events."""

    def map_event(self, event_name: str, payload: Mapping[str, Any]) -> MappedEventDTO | None: ...


class DictEventMapper:
    """Simple registry-based mapper for app event names."""

    def __init__(self, mapping: Mapping[str, EventKind]) -> None:
        self._mapping = {key.strip().lower(): value for key, value in mapping.items()}

    def map_event(self, event_name: str, payload: Mapping[str, Any]) -> MappedEventDTO | None:
        event_kind = self._mapping.get(event_name.strip().lower())
        if event_kind is None:
            return None
        return MappedEventDTO(event_kind=event_kind, payload=dict(payload))


class UnknownAppEventError(ValueError):
    """Raised when an app event cannot be resolved by the configured mapper."""


class SyncControlPlane:
    """Synchronous control-plane facade (no asyncio event loop required)."""

    def __init__(
        self,
        database_url: str = "sqlite:///./control_plane.db",
        *,
        engine: Engine | None = None,
        session_factory: sessionmaker[Session] | None = None,
        registry: RegistryProtocol | None = None,
        uow_factory: Callable[[Session], SyncSqlAlchemyUnitOfWork] | None = None,
        register_reference_models: bool = True,
    ) -> None:
        self._database_url = database_url
        self._registry = registry or ScopedModelRegistry()
        self._engine = engine or create_engine(database_url, future=True)
        self._session_factory = session_factory or sessionmaker(bind=self._engine, expire_on_commit=False, future=True)
        self._uow_factory = uow_factory or (lambda db: SyncSqlAlchemyUnitOfWork(db))
        if register_reference_models:
            register_models(registry=self._registry)

    def setup(self) -> None:
        """Create reference-model tables for control-plane state."""
        Base.metadata.create_all(self._engine)

    def close(self) -> None:
        self._engine.dispose()

    @contextmanager
    def session_scope(self) -> Iterator[Session]:
        """Context manager exposing a raw sync SQLAlchemy session."""
        with registry_scope(self._registry), self._session_factory() as db:
            yield db

    def create_session(
        self,
        name: str,
        *,
        max_cost: Decimal = Decimal("10000"),
        max_action_count: int = 50,
        execution_mode: ExecutionMode = ExecutionMode.DRY_RUN,
    ) -> UUID:
        with self.session_scope() as db:
            uow = self._uow_factory(db)
            cs = uow.session_repo.create_session(
                session_name=name,
                status=SessionStatus.CREATED,
                execution_mode=execution_mode,
                max_cost=max_cost,
                max_action_count=max_action_count,
            )
            uow.session_repo.create_seq_counter(cs.id)
            uow.commit()
            return cs.id

    def get_session(self, session_id: UUID) -> SessionState | None:
        with self.session_scope() as db:
            uow = self._uow_factory(db)
            return uow.session_repo.get_session(session_id)

    def list_sessions(
        self,
        *,
        statuses: list[SessionStatus] | None = None,
        limit: int = 50,
    ) -> list[SessionState]:
        with self.session_scope() as db:
            uow = self._uow_factory(db)
            return uow.session_repo.list_sessions(statuses=statuses, limit=limit)

    def check_budget(self, session_id: UUID, cost: Decimal = Decimal("0"), action_count: int = 1) -> bool:
        with self.session_scope() as db:
            uow = self._uow_factory(db)
            info = uow.session_repo.get_budget(session_id)
            return cost <= info.remaining_cost and action_count <= info.remaining_count

    def increment_budget(self, session_id: UUID, cost: Decimal, action_count: int = 1) -> None:
        with self.session_scope() as db:
            uow = self._uow_factory(db)
            uow.session_repo.increment_budget(session_id, cost, action_count)
            uow.commit()

    def get_remaining_budget(self, session_id: UUID) -> dict[str, Decimal | int]:
        with self.session_scope() as db:
            uow = self._uow_factory(db)
            info = uow.session_repo.get_budget(session_id)
            return {
                "remaining_cost": info.remaining_cost,
                "remaining_count": info.remaining_count,
                "used_cost": info.used_cost,
                "used_count": info.used_count,
                "max_cost": info.max_cost,
                "max_count": info.max_count,
            }

    def emit_event(
        self,
        session_id: UUID,
        event_kind: EventKind,
        payload: dict[str, Any],
        *,
        state_bearing: bool = False,
        agent_id: AgentId | None = None,
        correlation_id: UUID | None = None,
        routing_decision: dict[str, Any] | None = None,
        routing_reason: str | None = None,
        idempotency_key: IdempotencyKey | None = None,
    ) -> int:
        with self.session_scope() as db:
            uow = self._uow_factory(db)
            seq = uow.event_repo.append(
                session_id=session_id,
                event_kind=event_kind,
                payload=payload,
                state_bearing=state_bearing,
                agent_id=agent_id,
                correlation_id=correlation_id,
                routing_decision=routing_decision,
                routing_reason=routing_reason,
                idempotency_key=idempotency_key,
            )
            uow.commit()
            return seq

    def replay_events(self, session_id: UUID, *, after_seq: int = 0, limit: int = 100) -> list[EventFrame]:
        with self.session_scope() as db:
            uow = self._uow_factory(db)
            return uow.event_repo.replay(session_id, after_seq=after_seq, limit=limit)

    def emit_app_event(
        self,
        session_id: UUID,
        event_name: str,
        payload: Mapping[str, Any],
        *,
        mapper: AppEventMapper,
        unknown_policy: UnknownAppEventPolicy = UnknownAppEventPolicy.RAISE,
        state_bearing: bool | None = None,
        agent_id: AgentId | None = None,
        correlation_id: UUID | None = None,
        idempotency_key: IdempotencyKey | None = None,
    ) -> int | None:
        mapped = mapper.map_event(event_name, payload)
        if mapped is None:
            if unknown_policy == UnknownAppEventPolicy.IGNORE:
                return None
            raise UnknownAppEventError(f"Unknown app event: {event_name}")
        return self.emit_event(
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

    def complete_session(self, session_id: UUID) -> SessionLifecycleResult:
        with self.session_scope() as db:
            uow = self._uow_factory(db)
            uow.session_repo.update_session(
                session_id,
                status=SessionStatus.COMPLETED,
                active_cycle_id=None,
                updated_at=datetime.now(UTC),
            )
            uow.commit()
            session = uow.session_repo.get_session(session_id)
            if session is None:
                raise ValueError(f"Session not found after completion: {session_id}")
            return SessionLifecycleResult(session=session)

    def abort_session(
        self,
        session_id: UUID,
        *,
        reason: str = "Session aborted",
        abort_reason: AbortReason = AbortReason.OPERATOR_REQUEST,
    ) -> SessionLifecycleResult:
        with self.session_scope() as db:
            uow = self._uow_factory(db)
            uow.session_repo.update_session(
                session_id,
                status=SessionStatus.ABORTED,
                abort_reason=abort_reason,
                abort_details=reason,
                active_cycle_id=None,
                updated_at=datetime.now(UTC),
            )
            uow.commit()
            session = uow.session_repo.get_session(session_id)
            if session is None:
                raise ValueError(f"Session not found after abort: {session_id}")
            return SessionLifecycleResult(session=session)

    def kill(self, session_id: UUID, reason: str = "Kill switch triggered") -> KillResultDTO:
        return self._trigger_kill(KillSwitchScope.SESSION_ABORT, session_id=session_id, reason=reason)

    def kill_all(self, reason: str = "System halt") -> KillResultDTO:
        return self._trigger_kill(KillSwitchScope.SYSTEM_HALT, reason=reason)

    def _trigger_kill(
        self,
        scope: KillSwitchScope,
        *,
        session_id: UUID | None = None,
        reason: str = "Kill switch triggered",
    ) -> KillResultDTO:
        with self.session_scope() as db:
            uow = self._uow_factory(db)

            if scope == KillSwitchScope.SESSION_ABORT:
                if session_id is None:
                    raise ValueError("session_id required for session_abort")
                uow.session_repo.update_session(
                    session_id,
                    status=SessionStatus.ABORTED,
                    abort_reason=AbortReason.KILL_SWITCH,
                    abort_details=reason,
                    active_cycle_id=None,
                    updated_at=datetime.now(UTC),
                )
                denied = uow.approval_repo.deny_all_pending(session_id)
                uow.event_repo.append(
                    session_id,
                    EventKind.SESSION_ABORTED,
                    {"reason": reason, "tickets_denied": denied},
                    state_bearing=True,
                )
                uow.commit()
                return KillResultDTO(
                    scope=KillSwitchScope.SESSION_ABORT,
                    session_id=session_id,
                    tickets_denied=denied,
                )

            if scope == KillSwitchScope.SYSTEM_HALT:
                sessions = uow.session_repo.list_sessions(statuses=[SessionStatus.ACTIVE, SessionStatus.CREATED])
                denied_total = 0
                for cs in sessions:
                    uow.session_repo.update_session(
                        cs.id,
                        status=SessionStatus.ABORTED,
                        abort_reason=AbortReason.KILL_SWITCH,
                        abort_details=reason,
                        active_cycle_id=None,
                        updated_at=datetime.now(UTC),
                    )
                    denied_total += uow.approval_repo.deny_all_pending(cs.id)
                    uow.event_repo.append(
                        cs.id,
                        EventKind.KILL_SWITCH_TRIGGERED,
                        {"scope": "system_halt", "reason": reason},
                        state_bearing=True,
                    )
                uow.commit()
                return KillResultDTO(
                    scope=KillSwitchScope.SYSTEM_HALT,
                    sessions_aborted=len(sessions),
                    tickets_denied=denied_total,
                )

            raise ValueError(f"Unsupported kill scope for sync API: {scope}")


class ControlPlaneFacade:
    """High-level sync facade for host applications."""

    def __init__(
        self,
        control_plane: SyncControlPlane,
        *,
        mapper: AppEventMapper | None = None,
        unknown_policy: UnknownAppEventPolicy = UnknownAppEventPolicy.RAISE,
    ) -> None:
        self._cp = control_plane
        self._mapper = mapper
        self._unknown_policy = unknown_policy

    @classmethod
    def from_database_url(
        cls,
        database_url: str = "sqlite:///./control_plane.db",
        *,
        mapper: AppEventMapper | None = None,
        unknown_policy: UnknownAppEventPolicy = UnknownAppEventPolicy.RAISE,
        engine: Engine | None = None,
        session_factory: sessionmaker[Session] | None = None,
        registry: RegistryProtocol | None = None,
        uow_factory: Callable[[Session], SyncSqlAlchemyUnitOfWork] | None = None,
        register_reference_models: bool = True,
    ) -> ControlPlaneFacade:
        cp = SyncControlPlane(
            database_url=database_url,
            engine=engine,
            session_factory=session_factory,
            registry=registry,
            uow_factory=uow_factory,
            register_reference_models=register_reference_models,
        )
        return cls(cp, mapper=mapper, unknown_policy=unknown_policy)

    def setup(self) -> None:
        self._cp.setup()

    def close(self) -> None:
        self._cp.close()

    def open_session(
        self,
        name: str,
        *,
        max_cost: Decimal = Decimal("10000"),
        max_action_count: int = 50,
        execution_mode: ExecutionMode = ExecutionMode.DRY_RUN,
        command_id: IdempotencyKey | None = None,
    ) -> UUID:
        with self._cp.session_scope() as db:
            uow = self._cp._uow_factory(db)
            cached = self._get_cached_command_result(uow, command_id, CMD_OPEN_SESSION)
            if cached is not None:
                raw_session_id = cached.get("session_id")
                if not isinstance(raw_session_id, str):
                    raise ValueError("Invalid cached idempotency payload for open_session")
                return UUID(raw_session_id)
        session_id = self._cp.create_session(
            name=name,
            max_cost=max_cost,
            max_action_count=max_action_count,
            execution_mode=execution_mode,
        )
        with self._cp.session_scope() as db:
            uow = self._cp._uow_factory(db)
            self._record_command_result(
                uow,
                command_id,
                CMD_OPEN_SESSION,
                {"session_id": str(session_id)},
                session_id=session_id,
            )
            uow.commit()
        return session_id

    def close_session(
        self,
        session_id: UUID,
        *,
        final_event_kind: EventKind | None = None,
        payload: dict[str, Any] | None = None,
        command_id: IdempotencyKey | None = None,
    ) -> SessionLifecycleResult:
        with self._cp.session_scope() as db:
            uow = self._cp._uow_factory(db)
            cached = self._get_cached_command_result(uow, command_id, CMD_CLOSE_SESSION)
            if cached is not None:
                return SessionLifecycleResult.model_validate(cached)
        appended = 0
        if final_event_kind is not None:
            self._cp.emit_event(session_id, final_event_kind, payload or {}, state_bearing=True)
            appended = 1
        result = self._cp.complete_session(session_id)
        output = SessionLifecycleResult(session=result.session, events_appended=result.events_appended + appended)
        with self._cp.session_scope() as db:
            uow = self._cp._uow_factory(db)
            self._record_command_result(
                uow,
                command_id,
                CMD_CLOSE_SESSION,
                output.model_dump(mode="json"),
                session_id=session_id,
            )
            uow.commit()
        return output

    def abort_session(
        self,
        session_id: UUID,
        *,
        reason: str = "Session aborted",
        command_id: IdempotencyKey | None = None,
    ) -> SessionLifecycleResult:
        with self._cp.session_scope() as db:
            uow = self._cp._uow_factory(db)
            cached = self._get_cached_command_result(uow, command_id, CMD_ABORT_SESSION)
            if cached is not None:
                return SessionLifecycleResult.model_validate(cached)
        result = self._cp.abort_session(session_id, reason=reason)
        with self._cp.session_scope() as db:
            uow = self._cp._uow_factory(db)
            self._record_command_result(
                uow,
                command_id,
                CMD_ABORT_SESSION,
                result.model_dump(mode="json"),
                session_id=session_id,
            )
            uow.commit()
        return result

    def emit(
        self,
        session_id: UUID,
        event_kind: EventKind,
        payload: dict[str, Any],
        *,
        state_bearing: bool = False,
        agent_id: AgentId | None = None,
        correlation_id: UUID | None = None,
        routing_decision: dict[str, Any] | None = None,
        routing_reason: str | None = None,
        idempotency_key: IdempotencyKey | None = None,
        command_id: IdempotencyKey | None = None,
    ) -> int:
        with self._cp.session_scope() as db:
            uow = self._cp._uow_factory(db)
            cached = self._get_cached_command_result(uow, command_id, CMD_EMIT)
            if cached is not None:
                seq = cached.get("seq")
                if not isinstance(seq, int):
                    raise ValueError("Invalid cached idempotency payload for emit")
                return seq

        seq = self._cp.emit_event(
            session_id,
            event_kind,
            payload,
            state_bearing=state_bearing,
            agent_id=agent_id,
            correlation_id=correlation_id,
            routing_decision=routing_decision,
            routing_reason=routing_reason,
            idempotency_key=idempotency_key,
        )
        with self._cp.session_scope() as db:
            uow = self._cp._uow_factory(db)
            self._record_command_result(
                uow,
                command_id,
                CMD_EMIT,
                {"seq": seq},
                session_id=session_id,
            )
            uow.commit()
        return seq

    def create_ticket(
        self,
        session_id: UUID,
        proposal_id: UUID,
        timeout_at: datetime,
        *,
        command_id: IdempotencyKey | None = None,
    ) -> ApprovalTicketDTO:
        with self._cp.session_scope() as db:
            uow = self._cp._uow_factory(db)
            cached = self._get_cached_command_result(uow, command_id, CMD_CREATE_TICKET)
            if cached is not None:
                return ApprovalTicketDTO.model_validate(cached)
            ticket = uow.approval_repo.create_ticket(session_id, proposal_id, timeout_at)
            self._record_command_result(
                uow,
                command_id,
                CMD_CREATE_TICKET,
                ticket.model_dump(mode="json"),
                session_id=session_id,
            )
            uow.commit()
            return ticket

    def approve_ticket(
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
        with self._cp.session_scope() as db:
            uow = self._cp._uow_factory(db)
            cached = self._get_cached_command_result(uow, command_id, CMD_APPROVE_TICKET)
            if cached is not None:
                return ApprovalTicketDTO.model_validate(cached)
            ticket = uow.approval_repo.get_pending_ticket_for_update(ticket_id)
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
            uow.approval_repo.update_ticket(ticket_id, **fields)
            uow.proposal_repo.update_status(ticket.proposal_id, ProposalStatus.APPROVED)
            result = ticket.model_copy(update=fields)
            self._record_command_result(
                uow,
                command_id,
                CMD_APPROVE_TICKET,
                result.model_dump(mode="json"),
                session_id=ticket.session_id,
            )
            uow.commit()
            return result

    def deny_ticket(
        self,
        ticket_id: UUID,
        *,
        reason: str = "",
        command_id: IdempotencyKey | None = None,
    ) -> ApprovalTicketDTO:
        with self._cp.session_scope() as db:
            uow = self._cp._uow_factory(db)
            cached = self._get_cached_command_result(uow, command_id, CMD_DENY_TICKET)
            if cached is not None:
                return ApprovalTicketDTO.model_validate(cached)
            ticket = uow.approval_repo.get_pending_ticket_for_update(ticket_id)
            fields: ApprovalTicketUpdateFields = {
                "status": ApprovalStatus.DENIED,
                "decision_reason": reason,
                "decided_at": datetime.now(UTC),
            }
            uow.approval_repo.update_ticket(ticket_id, **fields)
            uow.proposal_repo.update_status(ticket.proposal_id, ProposalStatus.DENIED)
            result = ticket.model_copy(update=fields)
            self._record_command_result(
                uow,
                command_id,
                CMD_DENY_TICKET,
                result.model_dump(mode="json"),
                session_id=ticket.session_id,
            )
            uow.commit()
            return result

    def emit_app(
        self,
        session_id: UUID,
        event_name: str,
        payload: Mapping[str, Any],
        *,
        state_bearing: bool | None = None,
        agent_id: AgentId | None = None,
        correlation_id: UUID | None = None,
        idempotency_key: IdempotencyKey | None = None,
    ) -> int | None:
        if self._mapper is None:
            raise ValueError("No app event mapper configured")
        return self._cp.emit_app_event(
            session_id=session_id,
            event_name=event_name,
            payload=payload,
            mapper=self._mapper,
            unknown_policy=self._unknown_policy,
            state_bearing=state_bearing,
            agent_id=agent_id,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
        )

    def replay(self, session_id: UUID, *, after_seq: int = 0, limit: int = 100) -> list[EventFrame]:
        return self._cp.replay_events(session_id, after_seq=after_seq, limit=limit)

    def get_session(self, session_id: UUID) -> SessionState | None:
        return self._cp.get_session(session_id)

    def get_ticket(self, ticket_id: UUID) -> ApprovalTicketDTO | None:
        with self._cp.session_scope() as db:
            uow = self._cp._uow_factory(db)
            return uow.approval_repo.get_ticket(ticket_id)

    def list_tickets(
        self,
        *,
        session_id: UUID | None = None,
        statuses: list[ApprovalStatus] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> PageDTO[ApprovalTicketDTO]:
        with self._cp.session_scope() as db:
            uow = self._cp._uow_factory(db)
            rows = uow.approval_repo.list_tickets(
                session_id=session_id,
                statuses=statuses,
                limit=limit + 1,
                offset=offset,
            )
            has_more = len(rows) > limit
            return PageDTO(items=rows[:limit], next_offset=(offset + limit if has_more else None))

    def get_proposal(self, proposal_id: UUID) -> ActionProposalDTO | None:
        with self._cp.session_scope() as db:
            uow = self._cp._uow_factory(db)
            return uow.proposal_repo.get_proposal(proposal_id)

    def list_proposals(
        self,
        *,
        session_id: UUID | None = None,
        statuses: list[ProposalStatus] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> PageDTO[ActionProposalDTO]:
        with self._cp.session_scope() as db:
            uow = self._cp._uow_factory(db)
            rows = uow.proposal_repo.list_proposals(
                session_id=session_id,
                statuses=statuses,
                limit=limit + 1,
                offset=offset,
            )
            has_more = len(rows) > limit
            return PageDTO(items=rows[:limit], next_offset=(offset + limit if has_more else None))

    def get_state_change_feed(
        self,
        *,
        session_id: UUID | None = None,
        cursor: int = 0,
        limit: int = 100,
    ) -> StateChangePageDTO:
        with self._cp.session_scope() as db:
            uow = self._cp._uow_factory(db)
            rows = uow.event_repo.list_state_bearing_events(
                session_id=session_id,
                offset=cursor,
                limit=limit + 1,
            )
            has_more = len(rows) > limit
            items = [StateChangeDTO(cursor=cursor + idx + 1, event=row) for idx, row in enumerate(rows[:limit])]
            return StateChangePageDTO(items=items, next_cursor=(cursor + limit if has_more else None))

    def get_health_snapshot(self) -> SessionHealthDTO:
        created = self._cp.list_sessions(statuses=[SessionStatus.CREATED], limit=10_000)
        active = self._cp.list_sessions(statuses=[SessionStatus.ACTIVE], limit=10_000)
        paused = self._cp.list_sessions(statuses=[SessionStatus.PAUSED], limit=10_000)
        with self._cp.session_scope() as db:
            uow = self._cp._uow_factory(db)
            pending = uow.approval_repo.get_pending_tickets()
        sessions_with_cycles = sum(1 for session in created + active + paused if session.active_cycle_id is not None)
        return SessionHealthDTO(
            total_sessions=len(created) + len(active) + len(paused),
            active_sessions=len(active),
            created_sessions=len(created),
            paused_sessions=len(paused),
            sessions_with_active_cycles=sessions_with_cycles,
            pending_tickets=len(pending),
        )

    def check_budget(self, session_id: UUID, *, cost: Decimal = Decimal("0"), action_count: int = 1) -> bool:
        return self._cp.check_budget(session_id, cost=cost, action_count=action_count)

    def increment_budget(self, session_id: UUID, *, cost: Decimal, action_count: int = 1) -> None:
        self._cp.increment_budget(session_id, cost=cost, action_count=action_count)

    def get_remaining_budget(self, session_id: UUID) -> dict[str, Decimal | int]:
        return self._cp.get_remaining_budget(session_id)

    def kill_session(
        self,
        session_id: UUID,
        *,
        reason: str = "Kill switch triggered",
        command_id: IdempotencyKey | None = None,
    ) -> KillResultDTO:
        with self._cp.session_scope() as db:
            uow = self._cp._uow_factory(db)
            cached = self._get_cached_command_result(
                uow, command_id, kill_command_operation(KillSwitchScope.SESSION_ABORT)
            )
            if cached is not None:
                return KillResultDTO.model_validate(cached)
        result = self._cp.kill(session_id, reason=reason)
        with self._cp.session_scope() as db:
            uow = self._cp._uow_factory(db)
            self._record_command_result(
                uow,
                command_id,
                kill_command_operation(KillSwitchScope.SESSION_ABORT),
                result.model_dump(mode="json"),
                session_id=session_id,
            )
            uow.commit()
        return result

    def kill_system(self, *, reason: str = "System halt", command_id: IdempotencyKey | None = None) -> KillResultDTO:
        with self._cp.session_scope() as db:
            uow = self._cp._uow_factory(db)
            cached = self._get_cached_command_result(
                uow, command_id, kill_command_operation(KillSwitchScope.SYSTEM_HALT)
            )
            if cached is not None:
                return KillResultDTO.model_validate(cached)
        result = self._cp.kill_all(reason=reason)
        with self._cp.session_scope() as db:
            uow = self._cp._uow_factory(db)
            self._record_command_result(
                uow,
                command_id,
                kill_command_operation(KillSwitchScope.SYSTEM_HALT),
                result.model_dump(mode="json"),
            )
            uow.commit()
        return result

    def _get_cached_command_result(
        self,
        uow: SyncSqlAlchemyUnitOfWork,
        command_id: IdempotencyKey | None,
        operation: str,
    ) -> dict[str, object] | None:
        if command_id is None:
            return None
        cached = uow.command_repo.get_command(str(command_id))
        if cached is None:
            return None
        if cached.operation != operation:
            raise ValueError(f"Command id {command_id} already used for operation {cached.operation}")
        return cached.result

    def _record_command_result(
        self,
        uow: SyncSqlAlchemyUnitOfWork,
        command_id: IdempotencyKey | None,
        operation: str,
        result: dict[str, object],
        *,
        session_id: UUID | None = None,
    ) -> None:
        if command_id is None:
            return
        uow.command_repo.record_command(
            str(command_id),
            operation,
            result,
            session_id=session_id,
        )
