"""
Finance Agent Example: Trading and Payments Governance.

Demonstrates:
- Auto-approving trades below a risk/value threshold.
- Manual approval gates for wire transfers.
- Hard-blocking account closures.
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

DATABASE_URL = "sqlite+aiosqlite:///./finance_example.db"


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
                ActionTier.BLOCKED: [ActionName.CLOSE_ACCOUNT],
                ActionTier.ALWAYS_APPROVE: [ActionName.CHECK_BALANCE],
                ActionTier.AUTO_APPROVE: [],  # We'll use risk-based routing
                ActionTier.UNRESTRICTED: [ActionName.EXECUTE_TRADE, ActionName.WIRE_TRANSFER],
            },
            risk_limits={"max_weight_pct": Decimal("2000.0")},
            auto_approve_conditions={
                "max_risk_tier": RiskLevel.LOW,
                "max_weight": "1000.0",
                "min_score": "0.9",
                "dry_run_only": False,
            },
            execution_mode="live",
        )

        # 2. Initialize Engines
        sm = SessionManager(uow.session_repo)
        pid = await sm.create_policy(**policy.model_dump(mode="json", exclude={"id", "created_at"}))
        cs = await sm.create_session(session_name="finance-ops", max_cost=Decimal("5000.0"), policy_id=pid)
        router = ProposalRouter(PolicyEngine(policy))
        gate = ApprovalGate(EventStore(uow.event_repo), uow.approval_repo, uow.proposal_repo)
        budget = BudgetTracker(uow.session_repo)

        # 3. Scenarios
        tasks = [
            (ActionName.CHECK_BALANCE, "acc-123", 1.0, 0.5),  # Always Approved (List)
            (ActionName.EXECUTE_TRADE, "stock-AAPL", 500.0, 0.95),  # Auto Approved (Low Risk)
            (ActionName.WIRE_TRANSFER, "bank-xyz", 2500.0, 0.1),  # Manual Gate (High Risk)
        ]

        for action, res, weight, score in tasks:
            logger.info(f"\n[PROPOSE] {action} on {res}")
            dto = ActionProposal(
                session_id=cs.id,
                resource_id=res,
                resource_type="finance",
                decision=action,
                reasoning=f"finance automation for {action.value}",
                weight=Decimal(str(weight)),
                score=Decimal(str(score)),
            )

            route = router.route(dto)
            if route.tier == ActionTier.BLOCKED:
                logger.warning("  Result: BLOCKED")
                continue

            # Persistence & Logic
            prop = ActionProposal(
                session_id=cs.id,
                resource_id=res,
                resource_type="fin",
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

            if route.tier == ActionTier.AUTO_APPROVE or action == ActionName.CHECK_BALANCE:
                logger.info(f"  Result: AUTO-APPROVED ({route.tier})")
                await budget.increment(cs.id, cost=dto.weight)
                prop.status = ProposalStatus.EXECUTED.value
            else:
                logger.info(f"  Result: MANUAL GATE REQUIRED (Risk: {route.risk_level})")
                ticket = await gate.create_ticket(cs.id, prop.id)
                await gate.approve(ticket.id, decided_by="compliance-officer")
                await budget.increment(cs.id, cost=dto.weight)
                prop.status = ProposalStatus.EXECUTED.value

            logger.info("  Status: SUCCESS")

        await uow.commit()
    logger.info("\nFinance Example Completed.")


if __name__ == "__main__":
    asyncio.run(main())
