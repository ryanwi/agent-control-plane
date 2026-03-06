"""
Ghosted Agent Example: Timeout Escalation Validation.

Demonstrates:
- Proposing an action that requires manual approval.
- Simulating a human failing to respond within the timeout window.
- The TimeoutEscalation engine automatically expiring the ticket.
"""

import asyncio
import logging
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_control_plane import (
    ActionName,
    ActionProposal,
    ApprovalGate,
    AsyncSqlAlchemyUnitOfWork,
    EventStore,
    ExecutionMode,
    ModelRegistry,
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

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = "sqlite+aiosqlite:///./ghosted_example.db"


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
        es = EventStore(uow.event_repo)
        gate = ApprovalGate(es, uow.approval_repo, uow.proposal_repo)

        # Policy with a very short timeout (2 seconds)
        policy = PolicySnapshotDTO(
            action_tiers={
                "unrestricted": [ActionName.TERMINATE_INSTANCE],
                "blocked": [],
                "always_approve": [],
                "auto_approve": [],
            },
            execution_mode=ExecutionMode.LIVE,
            approval_timeout_seconds=2,
            auto_approve_conditions={
                "max_risk_tier": RiskLevel.LOW,
                "max_weight": "1.0",
                "min_score": "0.9",
                "dry_run_only": False,
            },
        )
        pid = await sm.create_policy(**policy.model_dump(mode="json", exclude={"id", "created_at"}))
        cs = await sm.create_session(session_name="ghosted-session", max_cost=Decimal("100"), policy_id=pid)
        router = ProposalRouter(PolicyEngine(policy))

        # 1. Propose action requiring approval
        logger.info("\n[PROPOSE] Agent requesting TERMINATE_INSTANCE...")
        dto = ActionProposalDTO(
            session_id=cs.id,
            resource_id="i-123",
            resource_type="ec2",
            decision=ActionName.TERMINATE_INSTANCE,
            reasoning="Routine termination",
            weight=Decimal("50.0"),
            score=Decimal("0.5"),
        )
        route = router.route(dto)

        prop = ActionProposal(
            session_id=cs.id,
            resource_id=dto.resource_id,
            resource_type=dto.resource_type,
            decision=dto.decision,
            reasoning=dto.reasoning,
            weight=dto.weight,
            score=dto.score,
            action_tier=route.tier.value,
            risk_level=route.risk_level.value,
            status=ProposalStatus.PENDING.value,
        )
        uow._session.add(prop)
        await uow._session.flush()

        # 2. Create ticket
        ticket = await gate.create_ticket(cs.id, prop.id, timeout_seconds=2)
        await uow.commit()
        logger.info(f"  Created ticket: {ticket.id}. Waiting for human...")

        # 3. Wait past the timeout
        logger.info("  (Simulating human ghosting for 5 seconds...)")
        await asyncio.sleep(5)

        # 4. Trigger Escalation
        logger.info("\n[TIMEOUT CHECK] Running ApprovalGate timeout check...")
        expired_count = await gate.expire_timed_out_tickets()
        await uow.commit()
        logger.info(f"  Expired {expired_count} ticket(s).")

        # Verify ticket state
        ApprovalTicket = ModelRegistry.get("ApprovalTicket")
        result = await uow._session.execute(select(ApprovalTicket).where(ApprovalTicket.id == ticket.id))
        updated_ticket = result.scalar_one()
        if updated_ticket.status == "expired":
            logger.info(f"  Status: SUCCESS (Ticket {ticket.id} correctly marked EXPIRED).")
        else:
            logger.error("  Status: FAILED (Ticket not expired).")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
