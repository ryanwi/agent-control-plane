"""
Rate-Limited Agent Example: Concurrency and Budget Stress Test.

Demonstrates:
- Launching 10 concurrent requests to update the same resource.
- ConcurrencyGuard preventing race conditions.
- BudgetTracker halting execution once limits are hit.
"""

import asyncio
import logging
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_control_plane import (
    AsyncSqlAlchemyUnitOfWork,
    BudgetTracker,
    ConcurrencyGuard,
    ExecutionMode,
    PolicySnapshotDTO,
    ReferenceBase,
    ResourceLockedError,
    SessionManager,
    register_models,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = "sqlite+aiosqlite:///./rate_limited_example.db"


async def concurrent_task(task_id, session_maker, session_id, resource_id, results):
    """A single thread attempting to lock and consume budget."""
    async with session_maker() as session:
        uow = AsyncSqlAlchemyUnitOfWork(session)
        guard = ConcurrencyGuard(uow.session_repo, uow.proposal_repo)
        budget = BudgetTracker(uow.session_repo)

        try:
            # 1. Try to lock resource
            await guard.check_resource_lock(session_id, resource_id)

            # 2. Try to lock cycle
            cycle_id = uuid4()
            await guard.acquire_cycle(session_id, cycle_id)

            try:
                # 3. Try to consume budget (Cost: 10 per action, Max budget: 30)
                if await budget.check_budget(session_id, cost=Decimal("10.0")):
                    await budget.increment(session_id, cost=Decimal("10.0"))
                    results.append(f"Task {task_id}: Executed successfully")
                else:
                    results.append(f"Task {task_id}: Budget exhausted")
            finally:
                await guard.release_cycle(session_id)

            await uow.commit()

        except ResourceLockedError:
            results.append(f"Task {task_id}: Blocked by ConcurrencyGuard (Resource Locked)")
        except Exception as e:
            results.append(f"Task {task_id}: Error - {str(e)}")


async def main():
    register_models()
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(ReferenceBase.metadata.drop_all)
        await conn.run_sync(ReferenceBase.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)

    # Setup Session
    async with session_maker() as session:
        uow = AsyncSqlAlchemyUnitOfWork(session)
        sm = SessionManager(uow.session_repo)
        policy = PolicySnapshotDTO(execution_mode=ExecutionMode.LIVE)
        pid = await sm.create_policy(**policy.model_dump(mode="json", exclude={"id", "created_at"}))
        # Total budget allows exactly 3 executions (30.0 cost)
        cs = await sm.create_session(session_name="rate-limit-session", max_cost=Decimal("30.0"), policy_id=pid)
        session_id = cs.id
        await uow.commit()

    logger.info("\n[STRESS TEST] Launching 10 concurrent execution attempts on the same resource...")

    # Launch concurrent tasks
    results = []
    tasks = [concurrent_task(i, session_maker, session_id, "shared-resource-1", results) for i in range(10)]
    await asyncio.gather(*tasks)

    logger.info("\n[RESULTS]")
    success_count = sum(1 for r in results if "Executed successfully" in r)
    budget_count = sum(1 for r in results if "Budget exhausted" in r)
    lock_count = sum(1 for r in results if "ConcurrencyGuard" in r)

    for r in sorted(results):
        logger.info(f"  {r}")

    logger.info("\nSummary:")
    logger.info(f"  Successful Executions (Expected: 3): {success_count}")
    logger.info(f"  Budget Rejections (Expected: >0): {budget_count}")
    logger.info(f"  Concurrency Locks (Expected: >0): {lock_count}")

    if success_count == 3:
        logger.info("  Status: SUCCESS (Budget and concurrency strictly enforced).")
    else:
        logger.error("  Status: FAILED")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
