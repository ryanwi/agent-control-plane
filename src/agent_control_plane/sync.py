"""First-class synchronous API for agent-control-plane."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable
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
from agent_control_plane.types.enums import (
    AbortReason,
    EventKind,
    ExecutionMode,
    KillSwitchScope,
    SessionStatus,
    UnknownAppEventPolicy,
)
from agent_control_plane.types.frames import EventFrame
from agent_control_plane.types.ids import AgentId, IdempotencyKey
from agent_control_plane.types.sessions import SessionState


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
    ) -> UUID:
        return self._cp.create_session(
            name=name,
            max_cost=max_cost,
            max_action_count=max_action_count,
            execution_mode=execution_mode,
        )

    def close_session(
        self,
        session_id: UUID,
        *,
        final_event_kind: EventKind | None = None,
        payload: dict[str, Any] | None = None,
    ) -> SessionLifecycleResult:
        appended = 0
        if final_event_kind is not None:
            self._cp.emit_event(session_id, final_event_kind, payload or {}, state_bearing=True)
            appended = 1
        result = self._cp.complete_session(session_id)
        return SessionLifecycleResult(session=result.session, events_appended=result.events_appended + appended)

    def abort_session(self, session_id: UUID, *, reason: str = "Session aborted") -> SessionLifecycleResult:
        return self._cp.abort_session(session_id, reason=reason)

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
    ) -> int:
        return self._cp.emit_event(
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

    def check_budget(self, session_id: UUID, *, cost: Decimal = Decimal("0"), action_count: int = 1) -> bool:
        return self._cp.check_budget(session_id, cost=cost, action_count=action_count)

    def increment_budget(self, session_id: UUID, *, cost: Decimal, action_count: int = 1) -> None:
        self._cp.increment_budget(session_id, cost=cost, action_count=action_count)

    def get_remaining_budget(self, session_id: UUID) -> dict[str, Decimal | int]:
        return self._cp.get_remaining_budget(session_id)

    def kill_session(self, session_id: UUID, *, reason: str = "Kill switch triggered") -> KillResultDTO:
        return self._cp.kill(session_id, reason=reason)

    def kill_system(self, *, reason: str = "System halt") -> KillResultDTO:
        return self._cp.kill_all(reason=reason)
