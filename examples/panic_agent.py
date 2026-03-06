"""
Panic Agent Example: Kill Switch Validation.

Demonstrates:
- Multiple active sessions running.
- A "Security Monitor" triggering a SYSTEM_HALT.
- Verification that all sessions are aborted and pending tickets are denied.
"""

import asyncio
import logging
from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_control_plane import (
    ActionName,
    ActionProposal,
    ApprovalGate,
    AsyncSqlAlchemyUnitOfWork,
    EventStore,
    ExecutionMode,
    KillSwitch,
    KillSwitchScope,
    PolicySnapshotDTO,
    ProposalStatus,
    ReferenceBase,
    SessionManager,
    SessionStatus,
    register_models,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = "sqlite+aiosqlite:///./panic_example.db"


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
        ks = KillSwitch(sm, es, uow.session_repo, uow.approval_repo)

        policy = PolicySnapshotDTO(execution_mode=ExecutionMode.LIVE)
        pid = await sm.create_policy(**policy.model_dump(mode="json", exclude={"id", "created_at"}))

        # 1. Setup multiple sessions with pending tickets
        sids = []
        for i in range(3):
            cs = await sm.create_session(session_name=f"agent-task-{i}", max_cost=Decimal("100"), policy_id=pid)
            sids.append(cs.id)

            # Create a pending ticket
            prop = ActionProposal(
                session_id=cs.id,
                resource_id=f"res-{i}",
                resource_type="sys",
                decision=ActionName.REBOOT_INSTANCE,
                reasoning="Pending reboot",
                weight=Decimal("1.0"),
                score=Decimal("0.5"),
                action_tier="unrestricted",
                risk_level="medium",
                status=ProposalStatus.PENDING.value,
            )
            uow._session.add(prop)
            await uow._session.flush()
            await gate.create_ticket(cs.id, prop.id)

        await uow.commit()
        logger.info("\n[SETUP] 3 sessions created and waiting for approval.")

        # 2. Trigger System Halt
        logger.info("\n🚨 [PANIC] Security breach detected! Triggering SYSTEM_HALT...")
        result = await ks.trigger(KillSwitchScope.SYSTEM_HALT, reason="Breach detected")
        logger.info(f"  KillSwitch result: {result}")

        # 3. Verify states
        logger.info("\n[VERIFY] Checking session states...")
        all_aborted = True
        for sid in sids:
            cs = await uow.session_repo.get_session(sid)
            if cs.status != SessionStatus.ABORTED:
                all_aborted = False
                logger.error(f"  Session {sid} is {cs.status} instead of ABORTED")

            pending = await uow.approval_repo.get_pending_tickets(session_id=sid)
            if len(pending) > 0:
                all_aborted = False
                logger.error(f"  Session {sid} still has pending tickets!")

        if all_aborted:
            logger.info("  Status: SUCCESS (All sessions aborted, all tickets denied).")
        else:
            logger.error("  Status: FAILED")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
