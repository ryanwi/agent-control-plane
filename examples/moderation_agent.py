"""
Content Moderation Agent Example: Community Safety Governance.

Demonstrates:
- Auto-approving post hiding for low-impact content moderation.
- Manual approval gates for banning users (high-impact).
- Budget limits to prevent mass-action errors.
"""

import asyncio
import logging
from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_control_plane import (
    ActionName,
    ActionProposal,
    ActionTier,
    ApprovalGate,
    AsyncSqlAlchemyUnitOfWork,
    EventStore,
    PolicyEngine,
    PolicySnapshot,
    ProposalRouter,
    ProposalStatus,
    ReferenceBase,
    RiskLevel,
    SessionManager,
    register_models,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = "sqlite+aiosqlite:///./moderation_example.db"


async def main():
    register_models()
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(ReferenceBase.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_maker() as session:
        uow = AsyncSqlAlchemyUnitOfWork(session)

        # 1. Define Policy
        policy = PolicySnapshot(
            action_tiers={
                ActionTier.ALWAYS_APPROVE: [ActionName.FLAG_CONTENT, ActionName.LOG_VIOLATION],
                ActionTier.AUTO_APPROVE: [],
                ActionTier.UNRESTRICTED: [ActionName.HIDE_POST, ActionName.BAN_USER],
            },
            risk_limits={"max_weight_pct": Decimal("20.0")},
            auto_approve_conditions={
                "max_risk_tier": RiskLevel.LOW,
                "max_weight": "5.0",
                "min_score": "0.95",
                "dry_run_only": False,
            },
            execution_mode="live",
        )

        # 2. Initialize Engines
        sm = SessionManager(uow.session_repo)
        pid = await sm.create_policy(**policy.model_dump(mode="json", exclude={"id", "created_at"}))
        cs = await sm.create_session(session_name="moderation-session", max_cost=Decimal("50.0"), policy_id=pid)
        router = ProposalRouter(PolicyEngine(policy))
        gate = ApprovalGate(EventStore(uow.event_repo), uow.approval_repo, uow.proposal_repo)

        # 3. Scenarios
        tasks = [
            (ActionName.HIDE_POST, "post-123", 1.0, 0.98),  # Auto (Low Risk)
            (ActionName.BAN_USER, "user-456", 10.0, 0.4),  # Gate (High Risk/Manual)
        ]

        for action, res, weight, score in tasks:
            logger.info(f"\n[PROPOSE] {action} on {res}")
            dto = ActionProposal(
                session_id=cs.id,
                resource_id=res,
                resource_type="user",
                decision=action,
                reasoning=f"moderation action for {res}",
                weight=Decimal(str(weight)),
                score=Decimal(str(score)),
            )

            route = router.route(dto)
            prop = ActionProposal(
                session_id=cs.id,
                resource_id=res,
                resource_type="mod",
                decision=action,
                reasoning=dto.reasoning,
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
                logger.info(f"  Result: MANUAL GATE REQUIRED (Risk: {route.risk_level})")
                ticket = await gate.create_ticket(cs.id, prop.id)
                await gate.approve(ticket.id, decided_by="human-mod")
                prop.status = ProposalStatus.EXECUTED.value

            logger.info("  Status: SUCCESS")

        await uow.commit()
    logger.info("\nModeration Example Completed.")


if __name__ == "__main__":
    asyncio.run(main())
