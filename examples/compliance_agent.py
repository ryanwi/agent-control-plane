"""
Strict Compliance Agent Example: Complex Asset Scoping.

Demonstrates:
- Using DefaultAssetClassifier to label resources.
- Policies auto-approving actions for "PUBLIC" assets.
- Forcing manual gates (high risk) for "PCI" assets.
"""

import asyncio
import logging
from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_control_plane import (
    ActionName,
    ActionTier,
    AsyncSqlAlchemyUnitOfWork,
    DefaultAssetClassifier,
    ExecutionMode,
    PolicyEngine,
    PolicySnapshotDTO,
    ProposalRouter,
    ReferenceBase,
    RiskLevel,
    SessionManager,
    register_models,
)
from agent_control_plane.types.proposals import ActionProposalDTO

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = "sqlite+aiosqlite:///./compliance_example.db"


async def main():
    register_models()
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(ReferenceBase.metadata.drop_all)
        await conn.run_sync(ReferenceBase.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_maker() as session:
        uow = AsyncSqlAlchemyUnitOfWork(session)
        sm = SessionManager(uow.session_repo)

        # Asset Classifier: Only resources with "PUBLIC" in the name are considered matched (Low risk).
        # Anything else (like "PCI") is unmatched and defaults to Medium/High risk.
        asset_classifier = DefaultAssetClassifier(patterns=frozenset(["PUBLIC"]))

        policy = PolicySnapshotDTO(
            action_tiers={"unrestricted": [ActionName.REBOOT_INSTANCE]},
            execution_mode=ExecutionMode.LIVE,
            auto_approve_conditions={
                "max_risk_tier": RiskLevel.LOW,
                "max_weight": "100.0",
                "min_score": "0.5",
                "dry_run_only": False,
            },
        )
        pid = await sm.create_policy(**policy.model_dump(mode="json", exclude={"id", "created_at"}))
        cs = await sm.create_session(session_name="compliance-session", max_cost=Decimal("100"), policy_id=pid)

        # Inject our custom classifier into the PolicyEngine
        router = ProposalRouter(PolicyEngine(policy, asset_classifier=asset_classifier))

        tasks = [
            ("PUBLIC-web-server", "Expected: AUTO_APPROVE (Matched asset -> Low Risk)"),
            ("PCI-db-server", "Expected: ALWAYS_APPROVE manual gate (Unmatched asset -> Medium Risk)"),
        ]

        logger.info("\n[COMPLIANCE] Testing asset-based routing...")
        for res, expectation in tasks:
            logger.info(f"\n  Proposing REBOOT_INSTANCE on {res}")
            logger.info(f"  {expectation}")

            dto = ActionProposalDTO(
                session_id=cs.id,
                resource_id=res,
                resource_type="server",
                decision=ActionName.REBOOT_INSTANCE,
                weight=Decimal("1.0"),
                score=Decimal("0.9"),
                reasoning="Patching",
            )
            route = router.route(dto)

            logger.info(f"  Result: {route.tier} (Risk assigned: {route.risk_level})")
            if "PUBLIC" in res and route.tier != ActionTier.AUTO_APPROVE:
                logger.error("  FAILED! Public asset should be auto-approved.")
            elif "PCI" in res and route.tier != ActionTier.ALWAYS_APPROVE:
                logger.error("  FAILED! PCI asset should require manual approval.")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
