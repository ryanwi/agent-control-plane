"""
Multi-Agent Delegation Example: Identity and Hand-off Governance.

Demonstrates:
- Registering multiple agents with specific capabilities.
- Governed delegation from one agent to another.
- Proposal routing with identity and capability validation.
"""

import asyncio
import logging
from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_control_plane import (
    ActionName,
    ActionProposal,
    AgentCapability,
    AgentMetadata,
    AgentRegistry,
    AsyncSqlAlchemyUnitOfWork,
    DelegationGuard,
    DelegationProposal,
    ExecutionMode,
    PolicyEngine,
    PolicySnapshot,
    ProposalRouter,
    ReferenceBase,
    RiskLevel,
    SessionManager,
    register_models,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = "sqlite+aiosqlite:///./delegation_example.db"


async def main():
    register_models()
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(ReferenceBase.metadata.drop_all)
        await conn.run_sync(ReferenceBase.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_maker() as session:
        uow = AsyncSqlAlchemyUnitOfWork(session)

        # 1. Setup Registry and Engines
        registry = AgentRegistry(uow.agent_repo)
        guard = DelegationGuard(registry, uow.agent_repo)
        sm = SessionManager(uow.session_repo)

        # 2. Register Agents
        dispatcher = AgentMetadata(
            id="dispatcher-01",
            name="Request Dispatcher",
            tags=["router"],
            capabilities=[AgentCapability(action=ActionName.STATUS)],
        )
        worker = AgentMetadata(
            id="worker-01",
            name="Infrastructure Worker",
            tags=["execution"],
            capabilities=[AgentCapability(action=ActionName.REBOOT_INSTANCE)],
        )

        await registry.register(dispatcher)
        await registry.register(worker)

        # 3. Governed Delegation
        logger.info("\n[DELEGATION] Dispatcher delegating task to Worker...")
        delegation = DelegationProposal(
            source_agent_id=dispatcher.id,
            target_agent_id=worker.id,
            task_description="Reboot unstable server srv-99",
            risk_score=0.2,
        )

        allowed = await guard.propose_delegation(delegation)
        if allowed:
            logger.info("  Result: Delegation ALLOWED and audited.")

        # 4. Propose Action with Identity Check
        policy = PolicySnapshot(
            action_tiers={"unrestricted": [ActionName.REBOOT_INSTANCE]},
            execution_mode=ExecutionMode.LIVE,
            auto_approve_conditions={
                "max_risk_tier": RiskLevel.LOW,
                "max_weight": "10.0",
                "min_score": "0.1",
                "dry_run_only": False,
            },
        )
        pid = await sm.create_policy(**policy.model_dump(mode="json", exclude={"id", "created_at"}))
        cs = await sm.create_session(session_name="maintenance-session", policy_id=pid)

        router = ProposalRouter(PolicyEngine(policy), agent_registry=registry)

        logger.info("\n[ROUTING] Worker proposing reboot action...")
        proposal = ActionProposal(
            session_id=cs.id,
            agent_id=worker.id,
            resource_id="srv-99",
            resource_type="server",
            decision=ActionName.REBOOT_INSTANCE,
            reasoning="Executing delegated task",
            weight=Decimal("1.0"),
            score=Decimal("0.9"),
        )

        decision = await router.route(proposal)
        logger.info(f"  Result: {decision.tier} (Resolution: {decision.resolution_step})")
        logger.info(f"  Reason: {decision.reason}")

        # 5. Unauthorized Action Check
        logger.info("\n[SECURITY] Dispatcher attempting unauthorized reboot...")
        bad_proposal = ActionProposal(
            session_id=cs.id,
            agent_id=dispatcher.id,
            resource_id="srv-99",
            resource_type="server",
            decision=ActionName.REBOOT_INSTANCE,
            reasoning="I'm not authorized but I'll try anyway",
            weight=Decimal("1.0"),
            score=Decimal("0.9"),
        )

        # Router will log a warning because dispatcher doesn't have REBOOT_INSTANCE capability
        await router.route(bad_proposal)

        await uow.commit()

    await engine.dispose()
    logger.info("\nMulti-Agent Example Completed.")


if __name__ == "__main__":
    asyncio.run(main())
