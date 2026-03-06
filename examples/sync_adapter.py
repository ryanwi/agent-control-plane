"""Sync adapter for calling the async control plane from synchronous code.

Use this when your host application (e.g. a sync agent loop, Flask/Django view,
or a Rails-adjacent Python process) can't use `await` directly.

    from sync_adapter import SyncControlPlane

    cp = SyncControlPlane("sqlite+aiosqlite:///./control_plane.db")
    cp.setup()  # creates tables, registers models

    session_id = cp.create_session("my-agent-run", max_cost=Decimal("50"))
    ok = cp.check_budget(session_id, cost=Decimal("1.50"))
    cp.kill(session_id)  # emergency stop
    cp.close()

The adapter owns its own SQLite database and event loop, completely separate
from your host application's database. This is intentional: governance state
(sessions, budgets, events, approvals) is orthogonal to domain data.

Note: for the default SQLite URL below, install `aiosqlite` first:
`uv pip install aiosqlite`.
"""

import asyncio
import threading
from decimal import Decimal
from uuid import UUID, uuid4

from agent_control_plane import (
    BudgetTracker,
    EventStore,
    KillSwitch,
    SessionManager,
)
from agent_control_plane.models.registry import ModelRegistry
from agent_control_plane.types.enums import KillSwitchScope


class SyncControlPlane:
    """Thin sync wrapper around the async control-plane engines.

    Runs an event loop on a background thread so async DB calls don't block
    the caller's thread. All public methods are synchronous.
    """

    def __init__(self, database_url: str = "sqlite+aiosqlite:///./control_plane.db") -> None:
        self._database_url = database_url
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session_factory = None
        self._engine = None

        # Engines
        self._session_manager = SessionManager()
        self._event_store = EventStore()
        self._budget_tracker = BudgetTracker()
        self._kill_switch = KillSwitch(self._session_manager, self._event_store)

    # -- Lifecycle -------------------------------------------------------

    def setup(self) -> None:
        """Start the background event loop, create tables, register models.

        Call this once at process startup.
        """
        if self._loop and self._loop.is_running():
            return
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._run(self._async_setup())

    def close(self) -> None:
        """Shut down the background event loop."""
        if self._loop and self._loop.is_running():
            self._run(self._async_close())
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=5)
        self._thread = None
        self._loop = None

    # -- Session management ----------------------------------------------

    def create_session(
        self,
        name: str,
        *,
        max_cost: Decimal = Decimal("10000"),
        max_action_count: int = 50,
        execution_mode: str = "dry_run",
    ) -> UUID:
        """Create a governance session. Returns the session ID."""

        async def _create():
            async with self._session_factory() as db:
                cs = await self._session_manager.create_session(
                    db,
                    session_name=name,
                    execution_mode=execution_mode,
                    max_cost=max_cost,
                    max_action_count=max_action_count,
                )
                await db.commit()
                return cs.id

        return self._run(_create())

    # -- Budget ----------------------------------------------------------

    def check_budget(
        self,
        session_id: UUID,
        cost: Decimal = Decimal("0"),
        action_count: int = 1,
    ) -> bool:
        """Check if the proposed action fits within session budget."""

        async def _check():
            async with self._session_factory() as db:
                return await self._budget_tracker.check_budget(
                    db, session_id, cost=cost, action_count=action_count
                )

        return self._run(_check())

    def increment_budget(
        self,
        session_id: UUID,
        cost: Decimal,
        action_count: int = 1,
    ) -> None:
        """Record budget consumption after executing an action."""

        async def _increment():
            async with self._session_factory() as db:
                await self._budget_tracker.increment(db, session_id, cost=cost, action_count=action_count)
                await db.commit()

        self._run(_increment())

    def get_remaining_budget(self, session_id: UUID) -> dict:
        """Get remaining cost and action count for a session."""

        async def _remaining():
            async with self._session_factory() as db:
                return await self._budget_tracker.get_remaining(db, session_id)

        return self._run(_remaining())

    # -- Kill switch -----------------------------------------------------

    def kill(self, session_id: UUID, reason: str = "Kill switch triggered") -> dict:
        """Emergency stop for a single session."""

        async def _kill():
            async with self._session_factory() as db:
                result = await self._kill_switch.trigger(
                    db, KillSwitchScope.SESSION_ABORT, session_id=session_id, reason=reason
                )
                await db.commit()
                return result

        return self._run(_kill())

    def kill_all(self, reason: str = "System halt") -> dict:
        """Emergency stop for ALL active sessions."""

        async def _kill_all():
            async with self._session_factory() as db:
                result = await self._kill_switch.trigger(db, KillSwitchScope.SYSTEM_HALT, reason=reason)
                await db.commit()
                return result

        return self._run(_kill_all())

    # -- Internals -------------------------------------------------------

    def _run(self, coro):
        """Submit a coroutine to the background loop and block until done."""
        if self._loop is None:
            raise RuntimeError("Call setup() before using the control plane")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=30)

    async def _async_setup(self):
        """Async initialization: create engine, tables, register models."""
        from datetime import UTC, datetime

        from sqlalchemy import DECIMAL, JSON, ForeignKey, String, Text
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
        from sqlalchemy.sql.sqltypes import Uuid

        class Base(DeclarativeBase):
            pass

        class ControlSession(Base):
            __tablename__ = "control_sessions"
            id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
            session_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
            status: Mapped[str] = mapped_column(String(20), nullable=False, default="created")
            execution_mode: Mapped[str] = mapped_column(String(20), nullable=False)
            asset_scope: Mapped[str | None] = mapped_column(String(50), nullable=True)
            max_cost: Mapped[Decimal] = mapped_column(DECIMAL(15, 2), nullable=False)
            used_cost: Mapped[Decimal] = mapped_column(DECIMAL(15, 2), nullable=False, default=Decimal("0"))
            max_action_count: Mapped[int] = mapped_column(nullable=False)
            used_action_count: Mapped[int] = mapped_column(nullable=False, default=0)
            active_policy_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
            active_cycle_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
            dry_run_session_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
            abort_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
            abort_details: Mapped[str | None] = mapped_column(Text, nullable=True)
            created_at: Mapped[datetime] = mapped_column(nullable=False, default=lambda: datetime.now(UTC))
            updated_at: Mapped[datetime | None] = mapped_column(nullable=True)

        class SessionSeqCounter(Base):
            __tablename__ = "session_seq_counters"
            id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
            session_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("control_sessions.id"))
            next_seq: Mapped[int] = mapped_column(nullable=False, default=1)

        class ControlEvent(Base):
            __tablename__ = "control_events"
            id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
            session_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("control_sessions.id"))
            seq: Mapped[int] = mapped_column(nullable=False)
            event_kind: Mapped[str] = mapped_column(String(50), nullable=False)
            agent_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
            correlation_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
            payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
            routing_decision: Mapped[dict | None] = mapped_column(JSON, nullable=True)
            routing_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
            idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
            created_at: Mapped[datetime] = mapped_column(nullable=False, default=lambda: datetime.now(UTC))

        class ApprovalTicket(Base):
            __tablename__ = "approval_tickets"
            id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
            session_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("control_sessions.id"))
            proposal_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
            status: Mapped[str] = mapped_column(String(20), nullable=False)
            decision_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
            decided_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
            decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
            decided_at: Mapped[datetime | None] = mapped_column(nullable=True)
            timeout_at: Mapped[datetime | None] = mapped_column(nullable=True)
            scope_resource_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
            scope_max_cost: Mapped[Decimal | None] = mapped_column(DECIMAL(15, 2), nullable=True)
            scope_max_count: Mapped[int | None] = mapped_column(nullable=True)
            scope_expiry: Mapped[datetime | None] = mapped_column(nullable=True)
            created_at: Mapped[datetime] = mapped_column(nullable=False, default=lambda: datetime.now(UTC))

        ModelRegistry.register("ControlSession", ControlSession)
        ModelRegistry.register("SessionSeqCounter", SessionSeqCounter)
        ModelRegistry.register("ControlEvent", ControlEvent)
        ModelRegistry.register("ApprovalTicket", ApprovalTicket)

        engine = create_async_engine(self._database_url, echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self._engine = engine
        self._session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _async_close(self) -> None:
        """Dispose resources owned by the adapter."""
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None


# ---------------------------------------------------------------------------
# Example usage
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cp = SyncControlPlane()
    cp.setup()

    # Create a session with a $50 budget and 10 action limit
    sid = cp.create_session("demo-agent-run", max_cost=Decimal("50"), max_action_count=10)
    print(f"Created session: {sid}")

    # Check budget before acting
    ok = cp.check_budget(sid, cost=Decimal("12.50"), action_count=1)
    print(f"Budget check (12.50): {ok}")  # True

    # Record the spend
    cp.increment_budget(sid, cost=Decimal("12.50"))
    remaining = cp.get_remaining_budget(sid)
    print(f"Remaining: ${remaining['remaining_cost']} cost, {remaining['remaining_count']} actions")

    # Check again with a large amount
    ok = cp.check_budget(sid, cost=Decimal("999"))
    print(f"Budget check (999): {ok}")  # False

    # Emergency stop
    result = cp.kill(sid, reason="operator requested stop")
    print(f"Kill switch result: {result}")

    cp.close()
    print("Done.")
