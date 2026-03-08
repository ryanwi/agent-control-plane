"""
Zombie Agent Example: Crash Recovery Validation.

Demonstrates:
- An agent crashing mid-cycle without releasing locks.
- A new agent instance starting up.
- CrashRecovery detecting the orphaned cycle and cleaning up.
"""

import asyncio
import logging
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_control_plane import (
    AsyncSqlAlchemyUnitOfWork,
    ConcurrencyGuard,
    CrashRecovery,
    EventStore,
    ExecutionMode,
    PolicySnapshot,
    ReferenceBase,
    SessionManager,
    SessionStatus,
    register_models,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = "sqlite+aiosqlite:///./zombie_example.db"


async def simulate_crash(session_maker):
    """Simulate an agent crashing mid-execution."""
    async with session_maker() as session:
        uow = AsyncSqlAlchemyUnitOfWork(session)
        sm = SessionManager(uow.session_repo)
        guard = ConcurrencyGuard(uow.session_repo, uow.proposal_repo)

        policy = PolicySnapshot(execution_mode=ExecutionMode.LIVE)
        pid = await sm.create_policy(**policy.model_dump(mode="json", exclude={"id", "created_at"}))
        cs = await sm.create_session(session_name="zombie-session", max_cost=Decimal("100"), policy_id=pid)

        # Transition to ACTIVE so CrashRecovery tracks it
        await uow.session_repo.update_session(cs.id, status=SessionStatus.ACTIVE.value)

        # Acquire cycle but never release it
        cycle_id = uuid4()
        await guard.acquire_cycle(cs.id, cycle_id)
        await uow.commit()

        logger.info(f"[CRASH] Agent acquired cycle {cycle_id} for session {cs.id} and is now dying...")
        # Simulate a hard crash by returning the session_id so the recovery can find it
        return cs.id


async def recover_agent(session_maker, crashed_session_id):
    """A new process/agent instance runs recovery."""
    async with session_maker() as session:
        uow = AsyncSqlAlchemyUnitOfWork(session)
        sm = SessionManager(uow.session_repo)
        es = EventStore(uow.event_repo)
        recovery = CrashRecovery(sm, es, uow.session_repo, uow.event_repo)

        logger.info("\n[RECOVERY] New agent instance starting up. Running crash recovery...")
        recovery_summary = await recovery.recover_on_startup()

        logger.info(f"[RECOVERY] Recovery summary: {recovery_summary}")

        # Verify the session is no longer locked
        cs = await uow.session_repo.get_session(crashed_session_id)
        if cs and cs.active_cycle_id is None:
            logger.info(f"  Status: SUCCESS (Session {cs.id} lock was safely cleared).")
        else:
            logger.error("  Status: FAILED (Session still locked).")
        await uow.commit()


async def main():
    register_models()
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(ReferenceBase.metadata.drop_all)
        await conn.run_sync(ReferenceBase.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)

    # 1. Start an agent that crashes
    crashed_session_id = await simulate_crash(session_maker)

    # 2. Start a new agent that recovers
    await recover_agent(session_maker, crashed_session_id)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
