"""First-class synchronous API for agent-control-plane."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agent_control_plane.models.reference import Base, register_models
from agent_control_plane.storage.sqlalchemy_sync import SyncSqlAlchemyUnitOfWork
from agent_control_plane.types.enums import (
    AbortReason,
    EventKind,
    ExecutionMode,
    KillSwitchScope,
    SessionStatus,
)


class KillResultDTO(BaseModel):
    scope: KillSwitchScope
    session_id: str | None = None
    sessions_aborted: int | None = None
    tickets_denied: int = 0


class SyncControlPlane:
    """Synchronous control-plane facade (no asyncio event loop required)."""

    def __init__(self, database_url: str = "sqlite:///./control_plane.db") -> None:
        self._database_url = database_url
        self._engine = create_engine(database_url, future=True)
        self._session_factory = sessionmaker(bind=self._engine, expire_on_commit=False, future=True)
        register_models()

    def setup(self) -> None:
        """Create reference-model tables for control-plane state."""
        Base.metadata.create_all(self._engine)

    def close(self) -> None:
        self._engine.dispose()

    def create_session(
        self,
        name: str,
        *,
        max_cost: Decimal = Decimal("10000"),
        max_action_count: int = 50,
        execution_mode: ExecutionMode = ExecutionMode.DRY_RUN,
    ) -> UUID:
        with self._session_factory() as db:
            uow = SyncSqlAlchemyUnitOfWork(db)
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

    def check_budget(self, session_id: UUID, cost: Decimal = Decimal("0"), action_count: int = 1) -> bool:
        with self._session_factory() as db:
            uow = SyncSqlAlchemyUnitOfWork(db)
            info = uow.session_repo.get_budget(session_id)
            return cost <= info.remaining_cost and action_count <= info.remaining_count

    def increment_budget(self, session_id: UUID, cost: Decimal, action_count: int = 1) -> None:
        with self._session_factory() as db:
            uow = SyncSqlAlchemyUnitOfWork(db)
            uow.session_repo.increment_budget(session_id, cost, action_count)
            uow.commit()

    def get_remaining_budget(self, session_id: UUID) -> dict[str, Decimal | int]:
        with self._session_factory() as db:
            uow = SyncSqlAlchemyUnitOfWork(db)
            info = uow.session_repo.get_budget(session_id)
            return {
                "remaining_cost": info.remaining_cost,
                "remaining_count": info.remaining_count,
                "used_cost": info.used_cost,
                "used_count": info.used_count,
                "max_cost": info.max_cost,
                "max_count": info.max_count,
            }

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
        with self._session_factory() as db:
            uow = SyncSqlAlchemyUnitOfWork(db)

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
                    session_id=str(session_id),
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
