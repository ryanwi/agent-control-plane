"""
Support Agent Example: Refunds and Customer Data Governance.

Demonstrates:
- Auto-approving refunds below $50 with high confidence.
- Manual gate for address changes (fraud risk).
- Budget limits on total refunds per session.
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
    BudgetTracker,
    EventStore,
    ExecutionMode,
    PolicyEngine,
    PolicySnapshotDTO,
    ProposalRouter,
    ProposalStatus,
    ReferenceBase,
    RiskLevel,
    SessionManager,
    register_models,
)
from agent_control_plane.types.proposals import ActionProposalDTO

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = "sqlite+aiosqlite:///./support_example.db"


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
                "always_approve": [ActionName.CHECK_ORDER_STATUS],
                "auto_approve": [],
                "unrestricted": [ActionName.REFUND, ActionName.CHANGE_ADDRESS],
            },
            risk_limits={"max_weight_pct": Decimal("100.0")},  # High limit for total refund
            auto_approve_conditions={
                "max_risk_tier": RiskLevel.LOW,
                "max_weight": "50.0",
                "min_score": "0.8",
                "dry_run_only": False,
            },
            execution_mode=ExecutionMode.LIVE,
        )

        # 2. Initialize Engines
        sm = SessionManager(uow.session_repo)
        pid = await sm.create_policy(**policy.model_dump(mode="json", exclude={"id", "created_at"}))
        cs = await sm.create_session(session_name="support-ops", max_cost=Decimal("200.0"), policy_id=pid)
        router = ProposalRouter(PolicyEngine(policy))
        gate = ApprovalGate(EventStore(uow.event_repo), uow.approval_repo, uow.proposal_repo)
        budget = BudgetTracker(uow.session_repo)

        # 3. Scenarios
        tasks = [
            (ActionName.REFUND, "order-99", 45.0, 0.9),  # Auto (Low Risk < 50)
            (ActionName.REFUND, "order-101", 150.0, 0.5),  # Gate (High Risk > 100)
            (ActionName.CHANGE_ADDRESS, "user-ryan", 1.0, 0.5),  # Gate (Medium Risk)
        ]

        for action, res, weight, score in tasks:
            logger.info(f"\n[PROPOSE] {action} on {res}")
            dto = ActionProposalDTO(
                session_id=cs.id,
                resource_id=res,
                resource_type="order",
                decision=action,
                reasoning=f"support workflow for {res}",
                weight=Decimal(str(weight)),
                score=Decimal(str(score)),
            )

            route = router.route(dto)
            prop = ActionProposal(
                session_id=cs.id,
                resource_id=res,
                resource_type="order",
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
                await budget.increment(cs.id, cost=dto.weight)
                prop.status = ProposalStatus.EXECUTED.value
            else:
                logger.info("  Result: MANUAL GATE REQUIRED")
                ticket = await gate.create_ticket(cs.id, prop.id)
                await gate.approve(ticket.id, decided_by="support-manager")
                await budget.increment(cs.id, cost=dto.weight)
                prop.status = ProposalStatus.EXECUTED.value

            logger.info("  Status: SUCCESS")

        await uow.commit()
    logger.info("\nSupport Example Completed.")


if __name__ == "__main__":
    asyncio.run(main())
