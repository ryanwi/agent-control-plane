"""
SRE Agent Example: Infrastructure Remediation Governance.

Demonstrates:
- Auto-approving low-risk remediation (restart pod).
- Hard-blocking destructive actions (delete database).
- Using ConcurrencyGuard for resource locking.
"""

import asyncio
import logging
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_control_plane import (
    ActionName,
    ActionTier,
    AsyncSqlAlchemyUnitOfWork,
    ConcurrencyGuard,
    PolicyEngine,
    PolicySnapshotDTO,
    ProposalRouter,
    ReferenceBase,
    RiskLevel,
    SessionManager,
    register_models,
)
from agent_control_plane.types.proposals import ActionProposalDTO

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = "sqlite+aiosqlite:///./sre_example.db"


async def main():
    register_models()
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(ReferenceBase.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_maker() as session:
        uow = AsyncSqlAlchemyUnitOfWork(session)

        # 1. Define Policy
        policy = PolicySnapshotDTO(
            action_tiers={
                ActionTier.BLOCKED: [ActionName.DELETE_CLUSTER, ActionName.WIPE_DISK],
                ActionTier.ALWAYS_APPROVE: [ActionName.FETCH_METRICS, ActionName.GET_LOGS],
                ActionTier.AUTO_APPROVE: [],
                ActionTier.UNRESTRICTED: [ActionName.RESTART_POD, ActionName.SCALE_UP],
            },
            risk_limits={"max_weight_pct": Decimal("50.0")},
            auto_approve_conditions={
                "max_risk_tier": RiskLevel.LOW,
                "max_weight": "10.0",
                "min_score": "0.7",
                "dry_run_only": False,
            },
            execution_mode="live",
        )

        # 2. Initialize Engines
        sm = SessionManager(uow.session_repo)
        pid = await sm.create_policy(**policy.model_dump(mode="json", exclude={"id", "created_at"}))
        cs = await sm.create_session(session_name="sre-remediation", max_cost=Decimal("100.0"), policy_id=pid)
        router = ProposalRouter(PolicyEngine(policy))
        guard = ConcurrencyGuard(uow.session_repo, uow.proposal_repo)

        # 3. Scenarios
        tasks = [
            (ActionName.RESTART_POD, "pod-web-01", 1.0, 0.9),  # Auto (Low Risk)
            (ActionName.DELETE_CLUSTER, "prod-db-cluster", 1.0, 0.5),  # BLOCKED
        ]

        for action, res, weight, score in tasks:
            logger.info(f"\n[PROPOSE] {action} on {res}")
            dto = ActionProposalDTO(
                session_id=cs.id,
                resource_id=res,
                resource_type="pod",
                decision=action,
                weight=Decimal(str(weight)),
                score=Decimal(str(score)),
            )

            route = router.route(dto)
            if route.tier == ActionTier.BLOCKED:
                logger.warning(f"  Result: BLOCKED (Step: {route.resolution_step})")
                continue

            # Lock check
            await guard.check_resource_lock(cs.id, res)

            # Execution
            if route.tier == ActionTier.AUTO_APPROVE:
                logger.info("  Result: AUTO-APPROVED")
                await guard.acquire_cycle(cs.id, cycle_id=uuid4())
                try:
                    logger.info(f"  Status: SUCCESS (Remediated {res})")
                finally:
                    await guard.release_cycle(cs.id)

        await uow.commit()
    logger.info("\nSRE Example Completed.")


if __name__ == "__main__":
    asyncio.run(main())
