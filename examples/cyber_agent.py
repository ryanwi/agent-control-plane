"""
Cybersecurity Agent Example: Incident Response Governance.

Demonstrates:
- Auto-approving host isolation when confidence is high.
- Manual approval gates for resetting admin credentials.
- Telemetry logging for all investigation steps.
"""

import asyncio
import logging
from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_control_plane import (
    ActionProposal,
    ActionTier,
    ApprovalGate,
    AsyncSqlAlchemyUnitOfWork,
    EventStore,
    PolicyEngine,
    PolicySnapshotDTO,
    ProposalRouter,
    ProposalStatus,
    ReferenceBase,
    SessionManager,
    register_models,
)
from agent_control_plane.types.proposals import ActionProposalDTO

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = "sqlite+aiosqlite:///./cyber_example.db"


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
                "always_approve": ["scan_vulnerability", "fetch_logs"],
                "auto_approve": [],
                "unrestricted": ["isolate_host", "reset_credentials"],
            },
            risk_limits={"max_weight_pct": Decimal("50.0")},
            auto_approve_conditions={
                "max_risk_tier": "low",
                "max_weight": "20.0",
                "min_score": "0.8",
                "dry_run_only": False,
            },
            execution_mode="live",
        )

        # 2. Initialize Engines
        sm = SessionManager(uow.session_repo)
        pid = await sm.create_policy(**policy.model_dump(mode="json", exclude={"id", "created_at"}))
        cs = await sm.create_session(session_name="incident-response", max_cost=Decimal("500.0"), policy_id=pid)
        router = ProposalRouter(PolicyEngine(policy))
        gate = ApprovalGate(EventStore(uow.event_repo), uow.approval_repo, uow.proposal_repo)

        # 3. Scenarios
        tasks = [
            ("isolate_host", "host-77", 10.0, 0.9),  # Auto (Low Risk < 20)
            ("reset_credentials", "admin-01", 5.0, 0.3),  # Gate (High Risk / Low Confidence)
        ]

        for action, res, weight, score in tasks:
            logger.info(f"\n[PROPOSE] {action} on {res}")
            dto = ActionProposalDTO(
                session_id=cs.id,
                resource_id=res,
                resource_type="host",
                decision=action,
                weight=Decimal(str(weight)),
                score=Decimal(str(score)),
            )

            route = router.route(dto)
            prop = ActionProposal(
                session_id=cs.id,
                resource_id=res,
                resource_type="cyber",
                decision=action,
                weight=dto.weight,
                score=dto.score,
                action_tier=route.tier.value,
                risk_level=route.risk_level.value,
                status=ProposalStatus.PENDING.value,
            )
            uow._session.add(prop)
            await uow._session.flush()

            if route.tier == ActionTier.AUTO_APPROVE:
                logger.info("  Result: AUTO-APPROVED")
                prop.status = ProposalStatus.EXECUTED.value
            else:
                logger.info("  Result: MANUAL GATE REQUIRED")
                ticket = await gate.create_ticket(cs.id, prop.id)
                await gate.approve(ticket.id, decided_by="soc-analyst")
                prop.status = ProposalStatus.EXECUTED.value

            logger.info("  Status: SUCCESS")

        await uow.commit()
    logger.info("\nCyber Example Completed.")


if __name__ == "__main__":
    asyncio.run(main())
