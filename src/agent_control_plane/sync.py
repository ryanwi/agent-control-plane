"""First-class synchronous API for agent-control-plane."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
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
from agent_control_plane.types.agentic import (
    ControlPlaneScorecardDTO,
    EvaluationResultDTO,
    GoalDTO,
    GuardrailDecisionDTO,
    HandoffResultDTO,
    PlanDTO,
    PlanProgressDTO,
    PlanStepDTO,
    RollbackResultDTO,
    SessionCheckpointDTO,
)
from agent_control_plane.types.approvals import ApprovalTicketDTO
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


def guardrail_event_kind(phase: GuardrailPhase) -> EventKind:
    if phase == GuardrailPhase.INPUT:
        return EventKind.GUARDRAIL_INPUT
    if phase == GuardrailPhase.TOOL:
        return EventKind.GUARDRAIL_TOOL
    return EventKind.GUARDRAIL_OUTPUT


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, round((percentile / 100.0) * (len(ordered) - 1))))
    return ordered[idx]


def _normalize_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


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

    def create_checkpoint(
        self,
        session_id: UUID,
        *,
        label: str,
        metadata: dict[str, object] | None = None,
        created_by: str = "system",
        command_id: IdempotencyKey | None = None,
    ) -> SessionCheckpointDTO:
        operation = "checkpoint:create"
        with self._cp.session_scope() as db:
            uow = self._cp._uow_factory(db)
            cached = self._get_cached_command_result(uow, command_id, operation)
            if cached is not None:
                return SessionCheckpointDTO.model_validate(cached)
            last = uow.event_repo.get_last_event(session_id)
            cp = SessionCheckpointDTO(
                session_id=session_id,
                event_seq=last.seq if last is not None else 0,
                label=label,
                metadata=dict(metadata or {}),
                created_by=created_by,
            )
            uow.event_repo.append(
                session_id,
                EventKind.CHECKPOINT_CREATED,
                cp.model_dump(mode="json"),
                state_bearing=True,
            )
            self._record_command_result(
                uow,
                command_id,
                operation,
                cp.model_dump(mode="json"),
                session_id=session_id,
            )
            uow.commit()
            return cp

    def list_checkpoints(self, session_id: UUID, *, limit: int = 50, offset: int = 0) -> PageDTO[SessionCheckpointDTO]:
        events = self._cp.replay_events(session_id, after_seq=0, limit=10_000)
        rows = [
            SessionCheckpointDTO.model_validate(e.payload)
            for e in events
            if e.event_kind == EventKind.CHECKPOINT_CREATED and isinstance(e.payload, dict)
        ]
        sliced = rows[offset : offset + limit + 1]
        has_more = len(sliced) > limit
        items = sliced[:limit]
        return PageDTO(items=items, next_offset=(offset + limit if has_more else None))

    def rollback_to_checkpoint(
        self,
        session_id: UUID,
        checkpoint_id: UUID,
        *,
        reason: str,
        command_id: IdempotencyKey | None = None,
    ) -> RollbackResultDTO:
        operation = "checkpoint:rollback"
        with self._cp.session_scope() as db:
            uow = self._cp._uow_factory(db)
            cached = self._get_cached_command_result(uow, command_id, operation)
            if cached is not None:
                return RollbackResultDTO.model_validate(cached)
            cps = self.list_checkpoints(session_id, limit=10_000, offset=0).items
            target = next((cp for cp in cps if cp.id == checkpoint_id), None)
            if target is None:
                raise ValueError(f"Checkpoint not found: {checkpoint_id}")
            last = uow.event_repo.get_last_event(session_id)
            from_seq = last.seq if last is not None else 0
            uow.event_repo.append(
                session_id,
                EventKind.ROLLBACK_REQUESTED,
                {"checkpoint_id": str(checkpoint_id), "reason": reason},
                state_bearing=True,
            )
            result = RollbackResultDTO(
                session_id=session_id,
                from_seq=from_seq,
                to_seq=target.event_seq,
                restored_fields=["session_state", "proposal_state", "approval_state"],
                events_appended=2,
            )
            uow.event_repo.append(
                session_id,
                EventKind.ROLLBACK_COMPLETED,
                result.model_dump(mode="json"),
                state_bearing=True,
            )
            self._record_command_result(
                uow,
                command_id,
                operation,
                result.model_dump(mode="json"),
                session_id=session_id,
            )
            uow.commit()
            return result

    def create_goal(
        self,
        session_id: UUID,
        *,
        name: str,
        description: str = "",
        metadata: dict[str, object] | None = None,
    ) -> GoalDTO:
        goal = GoalDTO(
            session_id=session_id,
            name=name,
            description=description,
            status=GoalStatus.ACTIVE,
            metadata=dict(metadata or {}),
        )
        self._cp.emit_event(session_id, EventKind.GOAL_CREATED, goal.model_dump(mode="json"), state_bearing=True)
        return goal

    def create_plan(self, session_id: UUID, goal_id: UUID, *, title: str, steps: list[str]) -> PlanDTO:
        plan_steps = [PlanStepDTO(plan_id=UUID(int=0), step_index=i, title=step) for i, step in enumerate(steps)]
        plan = PlanDTO(session_id=session_id, goal_id=goal_id, title=title, steps=plan_steps)
        plan.steps = [step.model_copy(update={"plan_id": plan.id}) for step in plan.steps]
        self._cp.emit_event(session_id, EventKind.PLAN_CREATED, plan.model_dump(mode="json"), state_bearing=True)
        return plan

    def start_plan_step(self, session_id: UUID, plan_id: UUID, *, step_index: int) -> PlanStepDTO:
        step = PlanStepDTO(
            plan_id=plan_id,
            step_index=step_index,
            title=f"step-{step_index}",
            status=PlanStepStatus.RUNNING,
        )
        self._cp.emit_event(session_id, EventKind.PLAN_STEP_STARTED, step.model_dump(mode="json"), state_bearing=True)
        return step

    def complete_plan_step(
        self, session_id: UUID, plan_id: UUID, *, step_index: int, notes: str | None = None
    ) -> PlanStepDTO:
        step = PlanStepDTO(
            plan_id=plan_id,
            step_index=step_index,
            title=f"step-{step_index}",
            status=PlanStepStatus.SUCCEEDED,
            notes=notes,
        )
        self._cp.emit_event(session_id, EventKind.PLAN_STEP_COMPLETED, step.model_dump(mode="json"), state_bearing=True)
        return step

    def get_plan_progress(self, session_id: UUID, goal_id: UUID) -> PlanProgressDTO:
        events = self._cp.replay_events(session_id, after_seq=0, limit=10_000)
        goal = next(
            (
                GoalDTO.model_validate(e.payload)
                for e in events
                if e.event_kind == EventKind.GOAL_CREATED
                and isinstance(e.payload, dict)
                and e.payload.get("id") == str(goal_id)
            ),
            None,
        )
        if goal is None:
            raise ValueError(f"Goal not found: {goal_id}")
        plan = next(
            (
                PlanDTO.model_validate(e.payload)
                for e in events
                if e.event_kind == EventKind.PLAN_CREATED
                and isinstance(e.payload, dict)
                and e.payload.get("goal_id") == str(goal_id)
            ),
            None,
        )
        completed_steps = 0
        failed_steps = 0
        running_steps = 0
        if plan is not None:
            total_steps = len(plan.steps)
            for e in events:
                if not isinstance(e.payload, dict):
                    continue
                if e.payload.get("plan_id") != str(plan.id):
                    continue
                if e.event_kind == EventKind.PLAN_STEP_COMPLETED:
                    completed_steps += 1
                elif e.event_kind == EventKind.PLAN_STEP_FAILED:
                    failed_steps += 1
                elif e.event_kind == EventKind.PLAN_STEP_STARTED:
                    running_steps += 1
        else:
            total_steps = 0
        return PlanProgressDTO(
            goal=goal,
            plan=plan,
            total_steps=total_steps,
            completed_steps=completed_steps,
            failed_steps=failed_steps,
            running_steps=running_steps,
        )

    def record_evaluation(
        self,
        session_id: UUID,
        *,
        operation: str,
        decision: EvaluationDecision,
        score: float,
        reasons: list[str],
        actions: list[str] | None = None,
    ) -> EvaluationResultDTO:
        result = EvaluationResultDTO(
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
        self._cp.emit_event(session_id, event_kind, result.model_dump(mode="json"), state_bearing=False)
        return result

    def apply_guardrail(
        self,
        session_id: UUID,
        *,
        phase: GuardrailPhase,
        allow: bool,
        policy_code: str,
        reason: str,
        metadata: dict[str, object] | None = None,
    ) -> GuardrailDecisionDTO:
        result = GuardrailDecisionDTO(
            session_id=session_id,
            phase=phase,
            allow=allow,
            policy_code=policy_code,
            reason=reason,
            metadata=dict(metadata or {}),
        )
        self._cp.emit_event(
            session_id,
            guardrail_event_kind(phase),
            result.model_dump(mode="json"),
            state_bearing=False,
        )
        return result

    def request_handoff(
        self,
        session_id: UUID,
        *,
        source_agent_id: str,
        target_agent_id: str,
        allowed_actions: list[str],
        accepted: bool = True,
        lease_seconds: int = 900,
        metadata: dict[str, object] | None = None,
    ) -> HandoffResultDTO:
        expires_at = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        result = HandoffResultDTO(
            session_id=session_id,
            source_agent_id=source_agent_id,
            target_agent_id=target_agent_id,
            allowed_actions=allowed_actions,
            accepted=accepted,
            lease_expires_at=expires_at,
            metadata=dict(metadata or {}),
        )
        event_kind = EventKind.HANDOFF_ACCEPTED if accepted else EventKind.HANDOFF_REJECTED
        self._cp.emit_event(session_id, event_kind, result.model_dump(mode="json"), state_bearing=False)
        return result

    def get_operational_scorecard(  # noqa: C901
        self,
        *,
        session_id: UUID | None = None,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> ControlPlaneScorecardDTO:
        sessions = [session_id] if session_id is not None else [s.id for s in self._cp.list_sessions(limit=10_000)]
        scorecard = ControlPlaneScorecardDTO()
        normalized_window_start = _normalize_utc(window_start) if window_start is not None else None
        normalized_window_end = _normalize_utc(window_end) if window_end is not None else None
        approval_latencies: list[float] = []
        rollback_latencies: list[float] = []
        total_cost = 0.0
        successful_actions = 0
        for sid in sessions:
            events = self._cp.replay_events(sid, after_seq=0, limit=10_000)
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
