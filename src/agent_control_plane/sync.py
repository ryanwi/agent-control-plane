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

from agent_control_plane._scorecard import ScorecardAcc, accumulate_scorecard_event, finalize_scorecard, normalize_utc
from agent_control_plane.engine.policy_engine import PolicyEngine
from agent_control_plane.engine.precondition_verifier import PreconditionVerifier, precondition_failure_payload
from agent_control_plane.engine.router import RoutingDecision
from agent_control_plane.engine.session_risk_accumulator import SessionRiskAccumulator
from agent_control_plane.models.reference import Base, register_models
from agent_control_plane.models.registry import (
    RegistryProtocol,
    ScopedModelRegistry,
    registry_scope,
)
from agent_control_plane.storage.sqlalchemy_sync import SyncSqlAlchemyUnitOfWork
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
from agent_control_plane.types.approvals import ApprovalScope, ApprovalTicket
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
from agent_control_plane.types.frames import EmitMetadata, EventFrame, EventMetadata
from agent_control_plane.types.ids import AgentId, IdempotencyKey, ResourceId
from agent_control_plane.types.policies import PolicySnapshot
from agent_control_plane.types.preconditions import (
    Precondition,
    PreconditionStateProvider,
    PreconditionStatus,
    PreconditionVerificationResult,
)
from agent_control_plane.types.proposals import ActionProposal
from agent_control_plane.types.query import Page, SessionHealth, StateChange, StateChangePage
from agent_control_plane.types.run_handle import RunHandle
from agent_control_plane.types.sessions import SessionState

CMD_OPEN_SESSION: Final[str] = "open_session"
CMD_CLOSE_SESSION: Final[str] = "close_session"
CMD_ABORT_SESSION: Final[str] = "abort_session"
CMD_EMIT: Final[str] = "emit"
CMD_CREATE_PROPOSAL: Final[str] = "create_proposal"
CMD_CREATE_TICKET: Final[str] = "create_ticket"
CMD_APPROVE_TICKET: Final[str] = "approve_ticket"
CMD_DENY_TICKET: Final[str] = "deny_ticket"
CMD_REVOKE_TICKET: Final[str] = "revoke_ticket"


def kill_command_operation(scope: KillSwitchScope) -> str:
    return f"kill:{scope.value}"


def guardrail_event_kind(phase: GuardrailPhase) -> EventKind:
    if phase == GuardrailPhase.INPUT:
        return EventKind.GUARDRAIL_INPUT
    if phase == GuardrailPhase.TOOL:
        return EventKind.GUARDRAIL_TOOL
    return EventKind.GUARDRAIL_OUTPUT


class ApprovalTicketUpdateFields(TypedDict, total=False):
    status: ApprovalStatus
    decision_type: ApprovalDecisionType
    decided_by: str
    decision_reason: str | None
    decided_at: datetime
    scope_resource_ids: list[ResourceId] | None
    scope_max_cost: Decimal | None
    scope_max_count: int | None
    scope_expiry: datetime | None
    revoked_by: str
    revocation_reason: str
    revoked_at: datetime


class KillResult(BaseModel):
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


class MappedEvent(BaseModel):
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

    def map_event(self, event_name: str, payload: Mapping[str, Any]) -> MappedEvent | None: ...


class DictEventMapper:
    """Simple registry-based mapper for app event names."""

    def __init__(self, mapping: Mapping[str, EventKind]) -> None:
        self._mapping = {key.strip().lower(): value for key, value in mapping.items()}

    def map_event(self, event_name: str, payload: Mapping[str, Any]) -> MappedEvent | None:
        event_kind = self._mapping.get(event_name.strip().lower())
        if event_kind is None:
            return None
        return MappedEvent(event_kind=event_kind, payload=dict(payload))


class UnknownAppEventError(ValueError):
    """Raised when an app event cannot be resolved by the configured mapper."""


class SyncControlPlane:  # pylint: disable=too-many-public-methods
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
        risk_accumulator: SessionRiskAccumulator | None = None,
    ) -> None:
        self._database_url = database_url
        self._registry = registry or ScopedModelRegistry()
        self._engine = engine or create_engine(database_url, future=True)
        self._session_factory = session_factory or sessionmaker(bind=self._engine, expire_on_commit=False, future=True)
        self._uow_factory = uow_factory or SyncSqlAlchemyUnitOfWork
        self._risk_accumulator = risk_accumulator
        if register_reference_models:
            register_models(registry=self._registry)

    @property
    def uow_factory(self) -> Callable[[Session], SyncSqlAlchemyUnitOfWork]:
        """Unit-of-work factory bound to this control plane's storage configuration."""
        return self._uow_factory

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

    def is_agent_revoked(self, session_id: UUID, agent_id: str) -> bool:
        """Whether ``agent_id`` is currently revoked for ``session_id`` (fail-closed gate)."""
        with self.session_scope() as db:
            uow = self._uow_factory(db)
            return uow.agent_repo.is_agent_revoked(session_id, agent_id)

    def revoke_agent(self, session_id: UUID, agent_id: str, *, reason: str = "") -> None:
        """Revoke an agent's authority for one session; records a state-bearing audit event."""
        with self.session_scope() as db:
            uow = self._uow_factory(db)
            uow.agent_repo.record_revocation(session_id, agent_id, reason)
            uow.event_repo.append(
                session_id=session_id,
                event_kind=EventKind.AGENT_REVOKED,
                payload={"agent_id": agent_id, "reason": reason},
                state_bearing=True,
            )
            uow.commit()

    def reinstate_agent(self, session_id: UUID, agent_id: str) -> None:
        """Clear a prior revocation, restoring the agent's authority for the session."""
        with self.session_scope() as db:
            uow = self._uow_factory(db)
            uow.agent_repo.clear_revocation(session_id, agent_id)
            uow.event_repo.append(
                session_id=session_id,
                event_kind=EventKind.AGENT_REINSTATED,
                payload={"agent_id": agent_id},
                state_bearing=True,
            )
            uow.commit()

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
        metadata: EventMetadata | None = None,
    ) -> int:
        with self.session_scope() as db:
            uow = self._uow_factory(db)
            seq = uow.event_repo.append(
                session_id=session_id,
                event_kind=event_kind,
                payload=payload,
                state_bearing=state_bearing,
                metadata=metadata,
            )
            uow.commit()
            return seq

    def verify_preconditions(
        self,
        session_id: UUID,
        preconditions: list[Precondition],
        *,
        proposal_id: UUID | None = None,
        action_id: str | None = None,
        providers: list[PreconditionStateProvider] | None = None,
        metadata: EventMetadata | None = None,
    ) -> PreconditionVerificationResult:
        """Verify preconditions and record a state-bearing failure event if they diverge."""

        with self.session_scope() as db:
            uow = self._uow_factory(db)
            verifier = PreconditionVerifier(event_store=None, providers=providers)
            result = verifier.check(preconditions)
            if result.status == PreconditionStatus.FAILED:
                uow.event_repo.append(
                    session_id=session_id,
                    event_kind=EventKind.PRECONDITION_FAILED,
                    payload=precondition_failure_payload(result, proposal_id=proposal_id, action_id=action_id),
                    state_bearing=True,
                    metadata=metadata,
                )
            uow.commit()
            return result

    def route_proposal(
        self,
        proposal: ActionProposal,
        policy_snapshot: PolicySnapshot,
    ) -> RoutingDecision:
        """Route a proposal through policy with optional session-risk accumulation.

        When a ``SessionRiskAccumulator`` was provided at construction, accumulated
        session risk is assessed before tier classification — the same escalation path
        as ``ProposalRouter`` in the async stack. A ``SESSION_RISK_ESCALATED`` event
        is written to the audit log on escalation.

        Initialise the accumulator without an ``event_store`` when using this method;
        ``SyncControlPlane`` handles event emission through its own sync event system.
        """
        import asyncio

        pe = PolicyEngine(policy_snapshot)
        original_risk = pe.classify_risk_level(proposal)
        risk_level = original_risk
        risk_escalation = None

        if self._risk_accumulator is not None:
            risk_escalation = asyncio.run(self._risk_accumulator.assess(proposal.session_id, proposal, original_risk))
            risk_level = risk_escalation.escalated_risk
            if risk_escalation.was_escalated and self._risk_accumulator._event_store is None:
                self.emit_event(
                    proposal.session_id,
                    EventKind.SESSION_RISK_ESCALATED,
                    {
                        "session_id": str(proposal.session_id),
                        "original_risk": original_risk.value,
                        "escalated_risk": risk_level.value,
                        "reasons": risk_escalation.escalation_reasons,
                    },
                    state_bearing=False,
                )

        tier = pe.classify_action_tier(proposal, risk_level)
        routing = pe.build_routing_reason(proposal, risk_level, tier)
        return RoutingDecision(
            tier=tier,
            risk_level=risk_level,
            reason=routing.reason,
            resolution_step=routing.resolution_step,
            risk_escalated=risk_escalation.was_escalated if risk_escalation is not None else False,
            risk_escalation=risk_escalation,
        )

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
        metadata: EventMetadata | None = None,
    ) -> int | None:
        mapped = mapper.map_event(event_name, payload)
        if mapped is None:
            if unknown_policy == UnknownAppEventPolicy.IGNORE:
                return None
            raise UnknownAppEventError(f"Unknown app event: {event_name}")
        m = metadata or EventMetadata()
        merged = EventMetadata(
            agent_id=m.agent_id if m.agent_id is not None else mapped.agent_id,
            correlation_id=m.correlation_id if m.correlation_id is not None else mapped.correlation_id,
            routing_decision=mapped.routing_decision,
            routing_reason=mapped.routing_reason,
            idempotency_key=m.idempotency_key if m.idempotency_key is not None else mapped.idempotency_key,
        )
        return self.emit_event(
            session_id=session_id,
            event_kind=mapped.event_kind,
            payload=mapped.payload,
            state_bearing=mapped.state_bearing if state_bearing is None else state_bearing,
            metadata=merged,
        )

    def activate_session(self, session_id: UUID) -> SessionLifecycleResult:
        with self.session_scope() as db:
            uow = self._uow_factory(db)
            cs = uow.session_repo.get_session(session_id)
            if cs is not None and cs.killed_at is not None:
                raise ValueError(f"Cannot activate a killed session (killed_at={cs.killed_at})")
            now = datetime.now(UTC)
            uow.session_repo.update_session(session_id, status=SessionStatus.ACTIVE, started_at=now, updated_at=now)
            uow.commit()
            session = uow.session_repo.get_session(session_id)
            if session is None:
                raise ValueError(f"Session not found after activation: {session_id}")
            return SessionLifecycleResult(session=session)

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

    def kill(self, session_id: UUID, reason: str = "Kill switch triggered") -> KillResult:
        return self._trigger_kill(KillSwitchScope.SESSION_ABORT, session_id=session_id, reason=reason)

    def kill_all(self, reason: str = "System halt") -> KillResult:
        return self._trigger_kill(KillSwitchScope.SYSTEM_HALT, reason=reason)

    def _trigger_kill(
        self,
        scope: KillSwitchScope,
        *,
        session_id: UUID | None = None,
        reason: str = "Kill switch triggered",
    ) -> KillResult:
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
                return KillResult(
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
                return KillResult(
                    scope=KillSwitchScope.SYSTEM_HALT,
                    sessions_aborted=len(sessions),
                    tickets_denied=denied_total,
                )

            raise ValueError(f"Unsupported kill scope for sync API: {scope}")


class _SyncGatewayBase:
    """Base providing idempotency helpers for sync gateways."""

    def __init__(self, cp: SyncControlPlane) -> None:
        self._cp = cp

    def _cached(
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

    def _record(
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
        uow.command_repo.record_command(str(command_id), operation, result, session_id=session_id)


class SessionGateway(_SyncGatewayBase):
    """Session lifecycle, event emission, and kill switches."""

    def __init__(
        self,
        cp: SyncControlPlane,
        mapper: AppEventMapper | None,
        unknown_policy: UnknownAppEventPolicy,
    ) -> None:
        super().__init__(cp)
        self._mapper = mapper
        self._unknown_policy = unknown_policy

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
            uow = self._cp.uow_factory(db)
            cached = self._cached(uow, command_id, CMD_OPEN_SESSION)
            if cached is not None:
                raw = cached.get("session_id")
                if not isinstance(raw, str):
                    raise ValueError("Invalid cached idempotency payload for open_session")
                return UUID(raw)
        session_id = self._cp.create_session(
            name=name, max_cost=max_cost, max_action_count=max_action_count, execution_mode=execution_mode
        )
        with self._cp.session_scope() as db:
            uow = self._cp.uow_factory(db)
            self._record(uow, command_id, CMD_OPEN_SESSION, {"session_id": str(session_id)}, session_id=session_id)
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
            uow = self._cp.uow_factory(db)
            cached = self._cached(uow, command_id, CMD_CLOSE_SESSION)
            if cached is not None:
                return SessionLifecycleResult.model_validate(cached)
        appended = 0
        if final_event_kind is not None:
            self._cp.emit_event(session_id, final_event_kind, payload or {}, state_bearing=True)
            appended = 1
        result = self._cp.complete_session(session_id)
        output = SessionLifecycleResult(session=result.session, events_appended=result.events_appended + appended)
        with self._cp.session_scope() as db:
            uow = self._cp.uow_factory(db)
            self._record(uow, command_id, CMD_CLOSE_SESSION, output.model_dump(mode="json"), session_id=session_id)
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
            uow = self._cp.uow_factory(db)
            cached = self._cached(uow, command_id, CMD_ABORT_SESSION)
            if cached is not None:
                return SessionLifecycleResult.model_validate(cached)
        result = self._cp.abort_session(session_id, reason=reason)
        with self._cp.session_scope() as db:
            uow = self._cp.uow_factory(db)
            self._record(uow, command_id, CMD_ABORT_SESSION, result.model_dump(mode="json"), session_id=session_id)
            uow.commit()
        return result

    def emit(
        self,
        session_id: UUID,
        event_kind: EventKind,
        payload: dict[str, Any],
        *,
        state_bearing: bool = False,
        metadata: EmitMetadata | None = None,
    ) -> int:
        meta = metadata or EmitMetadata()
        with self._cp.session_scope() as db:
            uow = self._cp.uow_factory(db)
            cached = self._cached(uow, meta.command_id, CMD_EMIT)
            if cached is not None:
                seq = cached.get("seq")
                if not isinstance(seq, int):
                    raise ValueError("Invalid cached idempotency payload for emit")
                return seq
        seq = self._cp.emit_event(
            session_id, event_kind, payload, state_bearing=state_bearing, metadata=meta.as_event_metadata()
        )
        with self._cp.session_scope() as db:
            uow = self._cp.uow_factory(db)
            self._record(uow, meta.command_id, CMD_EMIT, {"seq": seq}, session_id=session_id)
            uow.commit()
        return seq

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
            metadata=EventMetadata(
                agent_id=agent_id,
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            ),
        )

    def replay(self, session_id: UUID, *, after_seq: int = 0, limit: int = 100) -> list[EventFrame]:
        return self._cp.replay_events(session_id, after_seq=after_seq, limit=limit)

    def get_session(self, session_id: UUID) -> SessionState | None:
        return self._cp.get_session(session_id)

    def kill_session(
        self,
        session_id: UUID,
        *,
        reason: str = "Kill switch triggered",
        command_id: IdempotencyKey | None = None,
    ) -> KillResult:
        op = kill_command_operation(KillSwitchScope.SESSION_ABORT)
        with self._cp.session_scope() as db:
            uow = self._cp.uow_factory(db)
            cached = self._cached(uow, command_id, op)
            if cached is not None:
                return KillResult.model_validate(cached)
        result = self._cp.kill(session_id, reason=reason)
        with self._cp.session_scope() as db:
            uow = self._cp.uow_factory(db)
            self._record(uow, command_id, op, result.model_dump(mode="json"), session_id=session_id)
            uow.commit()
        return result

    def kill_system(self, *, reason: str = "System halt", command_id: IdempotencyKey | None = None) -> KillResult:
        op = kill_command_operation(KillSwitchScope.SYSTEM_HALT)
        with self._cp.session_scope() as db:
            uow = self._cp.uow_factory(db)
            cached = self._cached(uow, command_id, op)
            if cached is not None:
                return KillResult.model_validate(cached)
        result = self._cp.kill_all(reason=reason)
        with self._cp.session_scope() as db:
            uow = self._cp.uow_factory(db)
            self._record(uow, command_id, op, result.model_dump(mode="json"))
            uow.commit()
        return result


class ApprovalGateway(_SyncGatewayBase):
    """Approval tickets and action proposals."""

    def create_ticket(
        self,
        session_id: UUID,
        proposal_id: UUID,
        timeout_at: datetime,
        *,
        command_id: IdempotencyKey | None = None,
    ) -> ApprovalTicket:
        with self._cp.session_scope() as db:
            uow = self._cp.uow_factory(db)
            cached = self._cached(uow, command_id, CMD_CREATE_TICKET)
            if cached is not None:
                return ApprovalTicket.model_validate(cached)
            ticket = uow.approval_repo.create_ticket(session_id, proposal_id, timeout_at)
            self._record(uow, command_id, CMD_CREATE_TICKET, ticket.model_dump(mode="json"), session_id=session_id)
            uow.commit()
            return ticket

    def create_proposal(
        self,
        proposal: ActionProposal,
        *,
        command_id: IdempotencyKey | None = None,
    ) -> ActionProposal:
        with self._cp.session_scope() as db:
            uow = self._cp.uow_factory(db)
            cached = self._cached(uow, command_id, CMD_CREATE_PROPOSAL)
            if cached is not None:
                return ActionProposal.model_validate(cached)
            created = uow.proposal_repo.create_proposal(proposal)
            self._record(
                uow, command_id, CMD_CREATE_PROPOSAL, created.model_dump(mode="json"), session_id=proposal.session_id
            )
            uow.commit()
            return created

    def approve_ticket(
        self,
        ticket_id: UUID,
        *,
        decided_by: str = "operator",
        reason: str | None = None,
        decision_type: ApprovalDecisionType = ApprovalDecisionType.ALLOW_ONCE,
        scope: ApprovalScope | None = None,
        command_id: IdempotencyKey | None = None,
    ) -> ApprovalTicket:
        s = scope or ApprovalScope()
        with self._cp.session_scope() as db:
            uow = self._cp.uow_factory(db)
            cached = self._cached(uow, command_id, CMD_APPROVE_TICKET)
            if cached is not None:
                return ApprovalTicket.model_validate(cached)
            ticket = uow.approval_repo.get_pending_ticket_for_update(ticket_id)
            fields: ApprovalTicketUpdateFields = {
                "status": ApprovalStatus.APPROVED,
                "decision_type": decision_type,
                "decided_by": decided_by,
                "decision_reason": reason,
                "decided_at": datetime.now(UTC),
            }
            if decision_type == ApprovalDecisionType.ALLOW_FOR_SESSION:
                fields["scope_resource_ids"] = s.resource_ids if s.resource_ids else None
                fields["scope_max_cost"] = s.max_cost
                fields["scope_max_count"] = s.max_count
                fields["scope_expiry"] = s.expiry
            uow.approval_repo.update_ticket(ticket_id, **fields)
            uow.proposal_repo.update_status(ticket.proposal_id, ProposalStatus.APPROVED)
            result = ticket.model_copy(update=fields)
            self._record(
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
    ) -> ApprovalTicket:
        with self._cp.session_scope() as db:
            uow = self._cp.uow_factory(db)
            cached = self._cached(uow, command_id, CMD_DENY_TICKET)
            if cached is not None:
                return ApprovalTicket.model_validate(cached)
            ticket = uow.approval_repo.get_pending_ticket_for_update(ticket_id)
            fields: ApprovalTicketUpdateFields = {
                "status": ApprovalStatus.DENIED,
                "decision_reason": reason,
                "decided_at": datetime.now(UTC),
            }
            uow.approval_repo.update_ticket(ticket_id, **fields)
            uow.proposal_repo.update_status(ticket.proposal_id, ProposalStatus.DENIED)
            result = ticket.model_copy(update=fields)
            self._record(uow, command_id, CMD_DENY_TICKET, result.model_dump(mode="json"), session_id=ticket.session_id)
            uow.commit()
            return result

    def revoke_ticket(
        self,
        ticket_id: UUID,
        *,
        revoked_by: str = "system",
        reason: str = "",
        trigger: str = "manual",
        command_id: IdempotencyKey | None = None,
    ) -> ApprovalTicket:
        """Revoke an approved ticket, resetting the proposal to PENDING for re-approval.

        The caller is responsible for re-issuing a ticket via create_ticket() if manual
        re-approval is needed. This method only revokes; it does not create a new ticket.
        """
        with self._cp.session_scope() as db:
            uow = self._cp.uow_factory(db)
            cached = self._cached(uow, command_id, CMD_REVOKE_TICKET)
            if cached is not None:
                return ApprovalTicket.model_validate(cached)
            ticket = uow.approval_repo.get_ticket_for_update(ticket_id)
            if ticket.status != ApprovalStatus.APPROVED:
                raise ValueError(f"Ticket {ticket_id} is not approved (status={ticket.status})")
            revoked_at = datetime.now(UTC)
            fields: ApprovalTicketUpdateFields = {
                "status": ApprovalStatus.REVOKED,
                "revoked_by": revoked_by,
                "revocation_reason": reason,
                "revoked_at": revoked_at,
            }
            uow.approval_repo.update_ticket(ticket_id, **fields)
            uow.proposal_repo.update_status(ticket.proposal_id, ProposalStatus.PENDING)
            uow.event_repo.append(
                session_id=ticket.session_id,
                event_kind=EventKind.APPROVAL_REVOKED,
                payload={
                    "ticket_id": str(ticket_id),
                    "proposal_id": str(ticket.proposal_id),
                    "revoked_by": revoked_by,
                    "reason": reason,
                    "trigger": trigger,
                },
                state_bearing=True,
            )
            result = ticket.model_copy(update=fields)
            self._record(
                uow,
                command_id,
                CMD_REVOKE_TICKET,
                result.model_dump(mode="json"),
                session_id=ticket.session_id,
            )
            uow.commit()
            return result

    def get_ticket(self, ticket_id: UUID) -> ApprovalTicket | None:
        with self._cp.session_scope() as db:
            uow = self._cp.uow_factory(db)
            return uow.approval_repo.get_ticket(ticket_id)

    def list_tickets(
        self,
        *,
        session_id: UUID | None = None,
        statuses: list[ApprovalStatus] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[ApprovalTicket]:
        with self._cp.session_scope() as db:
            uow = self._cp.uow_factory(db)
            rows = uow.approval_repo.list_tickets(
                session_id=session_id, statuses=statuses, limit=limit + 1, offset=offset
            )
            has_more = len(rows) > limit
            return Page(items=rows[:limit], next_offset=(offset + limit if has_more else None))

    def get_proposal(self, proposal_id: UUID) -> ActionProposal | None:
        with self._cp.session_scope() as db:
            uow = self._cp.uow_factory(db)
            return uow.proposal_repo.get_proposal(proposal_id)

    def list_proposals(
        self,
        *,
        session_id: UUID | None = None,
        statuses: list[ProposalStatus] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[ActionProposal]:
        with self._cp.session_scope() as db:
            uow = self._cp.uow_factory(db)
            rows = uow.proposal_repo.list_proposals(
                session_id=session_id, statuses=statuses, limit=limit + 1, offset=offset
            )
            has_more = len(rows) > limit
            return Page(items=rows[:limit], next_offset=(offset + limit if has_more else None))


class BudgetGateway(_SyncGatewayBase):
    """Session cost and action-count budget."""

    def check_budget(self, session_id: UUID, *, cost: Decimal = Decimal("0"), action_count: int = 1) -> bool:
        return self._cp.check_budget(session_id, cost=cost, action_count=action_count)

    def increment_budget(self, session_id: UUID, *, cost: Decimal, action_count: int = 1) -> None:
        self._cp.increment_budget(session_id, cost=cost, action_count=action_count)

    def get_remaining_budget(self, session_id: UUID) -> dict[str, Decimal | int]:
        return self._cp.get_remaining_budget(session_id)


class AgentGateway(_SyncGatewayBase):
    """Per-session agent revocation: revoke / reinstate / is_revoked."""

    def revoke(self, session_id: UUID, agent_id: str, *, reason: str = "") -> None:
        self._cp.revoke_agent(session_id, agent_id, reason=reason)

    def reinstate(self, session_id: UUID, agent_id: str) -> None:
        self._cp.reinstate_agent(session_id, agent_id)

    def is_revoked(self, session_id: UUID, agent_id: str) -> bool:
        return self._cp.is_agent_revoked(session_id, agent_id)


class AgenticGateway(_SyncGatewayBase):
    """Agentic planning, evaluation, guardrails, and checkpoints."""

    def create_goal(
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
        self._cp.emit_event(session_id, EventKind.GOAL_CREATED, goal.model_dump(mode="json"), state_bearing=True)
        return goal

    def create_plan(self, session_id: UUID, goal_id: UUID, *, title: str, steps: list[str]) -> Plan:
        plan_steps = [PlanStep(plan_id=UUID(int=0), step_index=i, title=step) for i, step in enumerate(steps)]
        plan = Plan(session_id=session_id, goal_id=goal_id, title=title, steps=plan_steps)
        plan.steps = [step.model_copy(update={"plan_id": plan.id}) for step in plan.steps]
        self._cp.emit_event(session_id, EventKind.PLAN_CREATED, plan.model_dump(mode="json"), state_bearing=True)
        return plan

    def start_plan_step(self, session_id: UUID, plan_id: UUID, *, step_index: int) -> PlanStep:
        step = PlanStep(
            plan_id=plan_id, step_index=step_index, title=f"step-{step_index}", status=PlanStepStatus.RUNNING
        )
        self._cp.emit_event(session_id, EventKind.PLAN_STEP_STARTED, step.model_dump(mode="json"), state_bearing=True)
        return step

    def complete_plan_step(
        self, session_id: UUID, plan_id: UUID, *, step_index: int, notes: str | None = None
    ) -> PlanStep:
        step = PlanStep(
            plan_id=plan_id,
            step_index=step_index,
            title=f"step-{step_index}",
            status=PlanStepStatus.SUCCEEDED,
            notes=notes,
        )
        self._cp.emit_event(session_id, EventKind.PLAN_STEP_COMPLETED, step.model_dump(mode="json"), state_bearing=True)
        return step

    def get_plan_progress(self, session_id: UUID, goal_id: UUID) -> PlanProgress:
        events = self._cp.replay_events(session_id, after_seq=0, limit=10_000)
        goal = next(
            (
                Goal.model_validate(e.payload)
                for e in events
                if e.kind == EventKind.GOAL_CREATED
                and isinstance(e.payload, dict)
                and e.payload.get("id") == str(goal_id)
            ),
            None,
        )
        if goal is None:
            raise ValueError(f"Goal not found: {goal_id}")
        plan = next(
            (
                Plan.model_validate(e.payload)
                for e in events
                if e.kind == EventKind.PLAN_CREATED
                and isinstance(e.payload, dict)
                and e.payload.get("goal_id") == str(goal_id)
            ),
            None,
        )
        completed_steps = failed_steps = running_steps = 0
        if plan is not None:
            total_steps = len(plan.steps)
            for e in events:
                if not isinstance(e.payload, dict) or e.payload.get("plan_id") != str(plan.id):
                    continue
                if e.kind == EventKind.PLAN_STEP_COMPLETED:
                    completed_steps += 1
                elif e.kind == EventKind.PLAN_STEP_FAILED:
                    failed_steps += 1
                elif e.kind == EventKind.PLAN_STEP_STARTED:
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

    def record_evaluation(
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
    ) -> GuardrailDecision:
        result = GuardrailDecision(
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
        self._cp.emit_event(session_id, event_kind, result.model_dump(mode="json"), state_bearing=False)
        return result

    def create_checkpoint(
        self,
        session_id: UUID,
        *,
        label: str,
        metadata: dict[str, object] | None = None,
        created_by: str = "system",
        command_id: IdempotencyKey | None = None,
    ) -> SessionCheckpoint:
        operation = "checkpoint:create"
        with self._cp.session_scope() as db:
            uow = self._cp.uow_factory(db)
            cached = self._cached(uow, command_id, operation)
            if cached is not None:
                return SessionCheckpoint.model_validate(cached)
            last = uow.event_repo.get_last_event(session_id)
            cp = SessionCheckpoint(
                session_id=session_id,
                event_seq=last.seq if last is not None else 0,
                label=label,
                metadata=dict(metadata or {}),
                created_by=created_by,
            )
            uow.event_repo.append(
                session_id, EventKind.CHECKPOINT_CREATED, cp.model_dump(mode="json"), state_bearing=True
            )
            self._record(uow, command_id, operation, cp.model_dump(mode="json"), session_id=session_id)
            uow.commit()
            return cp

    def list_checkpoints(self, session_id: UUID, *, limit: int = 50, offset: int = 0) -> Page[SessionCheckpoint]:
        events = self._cp.replay_events(session_id, after_seq=0, limit=10_000)
        rows = [
            SessionCheckpoint.model_validate(e.payload)
            for e in events
            if e.kind == EventKind.CHECKPOINT_CREATED and isinstance(e.payload, dict)
        ]
        sliced = rows[offset : offset + limit + 1]
        has_more = len(sliced) > limit
        return Page(items=sliced[:limit], next_offset=(offset + limit if has_more else None))

    def rollback_to_checkpoint(
        self,
        session_id: UUID,
        checkpoint_id: UUID,
        *,
        reason: str,
        command_id: IdempotencyKey | None = None,
    ) -> RollbackResult:
        operation = "checkpoint:rollback"
        with self._cp.session_scope() as db:
            uow = self._cp.uow_factory(db)
            cached = self._cached(uow, command_id, operation)
            if cached is not None:
                return RollbackResult.model_validate(cached)
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
            result = RollbackResult(
                session_id=session_id,
                from_seq=from_seq,
                to_seq=target.event_seq,
                restored_fields=["session_state", "proposal_state", "approval_state"],
                events_appended=2,
            )
            uow.event_repo.append(
                session_id, EventKind.ROLLBACK_COMPLETED, result.model_dump(mode="json"), state_bearing=True
            )
            self._record(uow, command_id, operation, result.model_dump(mode="json"), session_id=session_id)
            uow.commit()
            return result


class ControlPlaneObserver(_SyncGatewayBase):
    """Read-only session queries, health checks, and operational metrics."""

    def list_sessions(self, *, statuses: list[SessionStatus] | None = None, limit: int = 50) -> list[SessionState]:
        return self._cp.list_sessions(statuses=statuses, limit=limit)

    def get_state_change_feed(
        self,
        *,
        session_id: UUID | None = None,
        cursor: int = 0,
        limit: int = 100,
    ) -> StateChangePage:
        with self._cp.session_scope() as db:
            uow = self._cp.uow_factory(db)
            rows = uow.event_repo.list_state_bearing_events(session_id=session_id, offset=cursor, limit=limit + 1)
            has_more = len(rows) > limit
            items = [StateChange(cursor=cursor + idx + 1, event=row) for idx, row in enumerate(rows[:limit])]
            return StateChangePage(items=items, next_cursor=(cursor + limit if has_more else None))

    def get_health_snapshot(self) -> SessionHealth:
        created = self._cp.list_sessions(statuses=[SessionStatus.CREATED], limit=10_000)
        active = self._cp.list_sessions(statuses=[SessionStatus.ACTIVE], limit=10_000)
        paused = self._cp.list_sessions(statuses=[SessionStatus.PAUSED], limit=10_000)
        with self._cp.session_scope() as db:
            uow = self._cp.uow_factory(db)
            pending = uow.approval_repo.get_pending_tickets()
        sessions_with_cycles = sum(1 for s in created + active + paused if s.active_cycle_id is not None)
        return SessionHealth(
            total_sessions=len(created) + len(active) + len(paused),
            active_sessions=len(active),
            created_sessions=len(created),
            paused_sessions=len(paused),
            sessions_with_active_cycles=sessions_with_cycles,
            pending_tickets=len(pending),
        )

    def get_operational_scorecard(
        self,
        *,
        session_id: UUID | None = None,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> ControlPlaneScorecard:
        sessions = [session_id] if session_id is not None else [s.id for s in self._cp.list_sessions(limit=10_000)]
        scorecard = ControlPlaneScorecard()
        ws = normalize_utc(window_start) if window_start is not None else None
        we = normalize_utc(window_end) if window_end is not None else None
        acc = ScorecardAcc()
        for sid in sessions:
            events = self._cp.replay_events(sid, after_seq=0, limit=10_000)
            for event in events:
                accumulate_scorecard_event(event, scorecard, acc, ws, we)
            scorecard.budget_denied_count += sum(
                1
                for e in events
                if e.kind == EventKind.KILL_SWITCH_TRIGGERED
                and isinstance(e.payload, dict)
                and e.payload.get("reason") in ("budget_denied", "budget_exhausted")
            )
        finalize_scorecard(scorecard, acc)
        return scorecard


class ControlPlaneFacade:
    """High-level sync entry point — composes focused gateway objects."""

    def __init__(
        self,
        control_plane: SyncControlPlane,
        *,
        mapper: AppEventMapper | None = None,
        unknown_policy: UnknownAppEventPolicy = UnknownAppEventPolicy.RAISE,
        risk_accumulator: SessionRiskAccumulator | None = None,
    ) -> None:
        self.sessions: SessionGateway = SessionGateway(control_plane, mapper, unknown_policy)
        self.approvals: ApprovalGateway = ApprovalGateway(control_plane)
        self.budget: BudgetGateway = BudgetGateway(control_plane)
        self.agentic: AgenticGateway = AgenticGateway(control_plane)
        self.agents: AgentGateway = AgentGateway(control_plane)
        self.observer: ControlPlaneObserver = ControlPlaneObserver(control_plane)
        self._cp = control_plane
        if risk_accumulator is not None:
            self._cp._risk_accumulator = risk_accumulator

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
        risk_accumulator: SessionRiskAccumulator | None = None,
    ) -> ControlPlaneFacade:
        cp = SyncControlPlane(
            database_url=database_url,
            engine=engine,
            session_factory=session_factory,
            registry=registry,
            uow_factory=uow_factory,
            risk_accumulator=risk_accumulator,
        )
        return cls(cp, mapper=mapper, unknown_policy=unknown_policy)

    def setup(self) -> None:
        self._cp.setup()

    def close(self) -> None:
        self._cp.close()

    def route_proposal(
        self,
        proposal: ActionProposal,
        policy_snapshot: PolicySnapshot,
    ) -> RoutingDecision:
        """Route a proposal through policy; delegates to ``SyncControlPlane.route_proposal``."""
        return self._cp.route_proposal(proposal, policy_snapshot)

    def verify_preconditions(
        self,
        session_id: UUID,
        preconditions: list[Precondition],
        *,
        proposal_id: UUID | None = None,
        action_id: str | None = None,
        providers: list[PreconditionStateProvider] | None = None,
        metadata: EventMetadata | None = None,
    ) -> PreconditionVerificationResult:
        """Verify preconditions immediately before host-managed execution.

        Non-MCP callers who manage execution outside of ``run()`` can call this
        method directly after kill-switch/budget checks and before performing
        side effects. When using ``run()``, pass ``preconditions`` there instead.
        """

        return self._cp.verify_preconditions(
            session_id,
            preconditions,
            proposal_id=proposal_id,
            action_id=action_id,
            providers=providers,
            metadata=metadata,
        )

    @contextmanager
    def run(
        self,
        name: str,
        *,
        max_cost: Decimal = Decimal("10000"),
        max_action_count: int = 50,
        execution_mode: ExecutionMode = ExecutionMode.DRY_RUN,
        preconditions: list[Precondition] | None = None,
        proposal_id: UUID | None = None,
        action_id: str | None = None,
        precondition_providers: list[PreconditionStateProvider] | None = None,
    ) -> Iterator[RunHandle]:
        """Open a tracked agent run and yield a handle for tagging.

        Opens a session, activates it, and closes it on exit. Tags accumulated
        via ``handle.tag()`` are written into the session's close payload.
        On exception: closes the session with SESSION_ABORTED and re-raises.

        If ``preconditions`` are supplied, they are verified after the kill-switch
        check (``activate_session``) and before the body executes. A failing
        verification aborts the session and raises ``RuntimeError("precondition_failed")``
        without entering the body.
        """
        session_id = self.sessions.open_session(
            name,
            max_cost=max_cost,
            max_action_count=max_action_count,
            execution_mode=execution_mode,
        )
        self._cp.activate_session(session_id)
        handle = RunHandle(session_id=session_id)
        try:
            if preconditions:
                prec_result = self._cp.verify_preconditions(
                    session_id,
                    preconditions,
                    proposal_id=proposal_id,
                    action_id=action_id,
                    providers=precondition_providers,
                )
                if prec_result.status == PreconditionStatus.FAILED:
                    raise RuntimeError("precondition_failed")
            yield handle
            self.sessions.close_session(
                session_id,
                final_event_kind=EventKind.EXECUTION_COMPLETED,
                payload=handle._tags or None,
            )
        except Exception as exc:
            self.sessions.close_session(
                session_id,
                final_event_kind=EventKind.SESSION_ABORTED,
                payload={"abort_reason": repr(exc), **handle._tags},
            )
            raise
