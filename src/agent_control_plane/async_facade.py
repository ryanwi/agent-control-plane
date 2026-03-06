"""First-class asynchronous facade for agent-control-plane."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from agent_control_plane.models.reference import Base, register_models
from agent_control_plane.models.registry import RegistryProtocol, ScopedModelRegistry, registry_scope
from agent_control_plane.storage.sqlalchemy_async import AsyncSqlAlchemyUnitOfWork
from agent_control_plane.sync import (
    AppEventMapper,
    KillResultDTO,
    SessionLifecycleResult,
    UnknownAppEventError,
)
from agent_control_plane.types.enums import (
    AbortReason,
    EventKind,
    ExecutionMode,
    KillSwitchScope,
    SessionStatus,
    UnknownAppEventPolicy,
)
from agent_control_plane.types.frames import EventFrame
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
    ) -> UUID:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            cs = await uow.session_repo.create_session(
                session_name=name,
                status=SessionStatus.CREATED,
                execution_mode=execution_mode,
                max_cost=max_cost,
                max_action_count=max_action_count,
            )
            await uow.session_repo.create_seq_counter(cs.id)
            await uow.commit()
            return cs.id

    async def get_session(self, session_id: UUID) -> SessionState | None:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
            return await uow.session_repo.get_session(session_id)

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
        agent_id: str | None = None,
        correlation_id: UUID | None = None,
        routing_decision: dict[str, object] | None = None,
        routing_reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> int:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
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
            await uow.commit()
            return seq

    async def emit_app(
        self,
        session_id: UUID,
        event_name: str,
        payload: Mapping[str, object],
        *,
        state_bearing: bool | None = None,
        agent_id: str | None = None,
        correlation_id: UUID | None = None,
        idempotency_key: str | None = None,
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

    async def close_session(
        self,
        session_id: UUID,
        *,
        final_event_kind: EventKind | None = None,
        payload: dict[str, object] | None = None,
    ) -> SessionLifecycleResult:
        appended = 0
        if final_event_kind is not None:
            await self.emit(session_id, final_event_kind, dict(payload or {}), state_bearing=True)
            appended = 1
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
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
            return SessionLifecycleResult(session=session, events_appended=appended)

    async def abort_session(
        self,
        session_id: UUID,
        *,
        reason: str = "Session aborted",
        abort_reason: AbortReason = AbortReason.OPERATOR_REQUEST,
    ) -> SessionLifecycleResult:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
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
            return SessionLifecycleResult(session=session)

    async def kill_session(self, session_id: UUID, *, reason: str = "Kill switch triggered") -> KillResultDTO:
        return await self._trigger_kill(KillSwitchScope.SESSION_ABORT, session_id=session_id, reason=reason)

    async def kill_system(self, *, reason: str = "System halt") -> KillResultDTO:
        return await self._trigger_kill(KillSwitchScope.SYSTEM_HALT, reason=reason)

    async def _trigger_kill(
        self,
        scope: KillSwitchScope,
        *,
        session_id: UUID | None = None,
        reason: str = "Kill switch triggered",
    ) -> KillResultDTO:
        async with self.session_scope() as db:
            uow = self._uow_factory(db)
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
                await uow.commit()
                return KillResultDTO(
                    scope=KillSwitchScope.SESSION_ABORT, session_id=str(session_id), tickets_denied=denied
                )

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
                await uow.commit()
                return KillResultDTO(
                    scope=KillSwitchScope.SYSTEM_HALT,
                    sessions_aborted=len(sessions),
                    tickets_denied=denied_total,
                )

            raise ValueError(f"Unsupported kill scope for async API: {scope}")
