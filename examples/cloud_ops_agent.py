"""
Cloud Operations Agent Example using agent-control-plane.

This example simulates an infrastructure management agent that performs:
- describe_resources: Always approved (read-only).
- stop_instance: Auto-approved (reversible, medium risk).
- terminate_instance: Requires manual approval (destructive, high risk).
- wipe_database: Blocked by policy.

It demonstrates:
1. Policy-based routing.
2. Manual approval gates for destructive actions.
3. Resource locking to prevent concurrent conflicts.
4. Budget tracking for total infrastructure "risk cost".
5. Event sourcing for audit trails.

Run:
    uv run python examples/cloud_ops_agent.py
"""

import asyncio
import logging
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_control_plane import (
    ActionName,
    ActionProposal,
    ActionTier,
    ApprovalDecisionType,
    ApprovalGate,
    AsyncSqlAlchemyUnitOfWork,
    BudgetTracker,
    ConcurrencyGuard,
    EventKind,
    EventStore,
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

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = "sqlite+aiosqlite:///./cloud_ops_example.db"


def create_cloud_policy() -> PolicySnapshotDTO:
    """Define a governance policy for cloud operations."""
    return PolicySnapshotDTO(
        action_tiers={
            ActionTier.BLOCKED: [ActionName.WIPE_DATABASE, ActionName.DELETE_VBC],
            ActionTier.ALWAYS_APPROVE: [ActionName.DESCRIBE_RESOURCES, ActionName.LIST_INSTANCES],
            ActionTier.AUTO_APPROVE: [ActionName.STOP_INSTANCE, ActionName.START_INSTANCE, ActionName.REBOOT_INSTANCE],
            ActionTier.UNRESTRICTED: [],  # Actions not listed here fall to risk-based routing
        },
        risk_limits={
            "max_risk_score": "1000",
            "max_weight_pct": "20.0",
        },
        execution_mode="live",
        approval_timeout_seconds=120,
        auto_approve_conditions={
            "max_risk_tier": RiskLevel.LOW,
            "dry_run_only": False,
            "max_weight": "10.0",
            "min_score": "0.9",
        },
    )


class CloudOpsAgent:
    def __init__(self, uow: AsyncSqlAlchemyUnitOfWork):
        self.uow = uow
        self.agent_id = "cloud-ops-bot-01"

        # Engines
        self.session_manager = SessionManager(uow.session_repo)
        self.event_store = EventStore(uow.event_repo)
        self.approval_gate = ApprovalGate(self.event_store, uow.approval_repo, uow.proposal_repo)
        self.budget = BudgetTracker(uow.session_repo)
        self.guard = ConcurrencyGuard(uow.session_repo, uow.proposal_repo)

    async def initialize(self, policy_snapshot: PolicySnapshotDTO):
        """Setup the session and router."""
        policy_id = await self.session_manager.create_policy(
            action_tiers=policy_snapshot.action_tiers.model_dump(mode="json"),
            risk_limits=policy_snapshot.risk_limits.model_dump(mode="json"),
            execution_mode=policy_snapshot.execution_mode.value,
            auto_approve_conditions=policy_snapshot.auto_approve_conditions.model_dump(mode="json"),
        )
        self.session = await self.session_manager.create_session(
            session_name=f"prod-infra-maintenance-{uuid4().hex[:8]}",
            execution_mode=policy_snapshot.execution_mode.value,
            max_cost=Decimal("100.0"),  # Total "risk budget"
            max_action_count=50,
            policy_id=policy_id,
        )
        self.router = ProposalRouter(PolicyEngine(policy_snapshot))
        logger.info(f"Initialized CloudOpsAgent with Session: {self.session.id}")

    async def run_task(self, action_name: ActionName, resource_id: str, risk_score: float = 0.5, weight: float = 1.0):
        """Simulate proposing and executing a cloud task."""
        logger.info(f"\n--- Task: {action_name} on {resource_id} ---")

        proposal_dto = ActionProposalDTO(
            session_id=self.session.id,
            resource_id=resource_id,
            resource_type="cloud_resource",
            decision=action_name,
            reasoning=f"Automated maintenance for {resource_id}",
            weight=Decimal(str(weight)),
            score=Decimal(str(risk_score)),
        )

        # 1. Route through Policy Engine
        route = self.router.route(proposal_dto)
        if route.tier == ActionTier.BLOCKED:
            logger.warning(f"ACTION BLOCKED: {action_name} is forbidden.")
            return

        # 2. Acquire Resource Lock
        try:
            await self.guard.check_resource_lock(self.session.id, resource_id)
        except Exception as e:
            logger.error(f"RESOURCE LOCKED: {resource_id} is currently being modified: {e}")
            return

        # 3. Create Proposal Record
        action = ActionProposal(
            session_id=self.session.id,
            resource_id=resource_id,
            resource_type="cloud_resource",
            decision=action_name,
            reasoning=proposal_dto.reasoning,
            weight=proposal_dto.weight,
            score=proposal_dto.score,
            action_tier=route.tier.value,
            risk_level=route.risk_level.value,
            status=ProposalStatus.PENDING.value,
        )
        self.uow._session.add(action)
        await self.uow._session.flush()

        # 4. Handle Approvals
        is_approved = False
        if route.tier == ActionTier.AUTO_APPROVE and route.risk_level == RiskLevel.LOW:
            logger.info("Auto-approving low-risk reversible action.")
            is_approved = True
        elif route.tier == ActionTier.ALWAYS_APPROVE and action_name in [
            ActionName.DESCRIBE_RESOURCES,
            ActionName.LIST_INSTANCES,
        ]:
            logger.info("Auto-approving read-only action from policy list.")
            is_approved = True
        elif route.tier == ActionTier.BLOCKED:
            logger.warning(f"Action {action_name} is BLOCKED.")
            return
        else:
            # Current library behavior: ALWAYS_APPROVE for high/medium risk often means manual gate needed
            logger.info(
                f"Action '{action_name}' (Tier: {route.tier}, Risk: {route.risk_level}) requires manual approval."
            )
            ticket = await self.approval_gate.create_ticket(self.session.id, action.id)
            is_approved = await self.simulate_human_review(ticket.id, action)

        if is_approved:
            await self.execute(action)
        else:
            logger.warning(f"Action '{action_name}' was NOT approved.")

    async def simulate_human_review(self, ticket_id, action: ActionProposal) -> bool:
        """Simulate a human operator reviewing the destructive action."""
        logger.info(f"[HUMAN OPS] Reviewing ticket {ticket_id} for {action.decision}...")

        # In this demo, we approve everything except if it's the 3rd termination (arbitrary rule)
        await self.approval_gate.approve(
            ticket_id,
            decision_type=ApprovalDecisionType.ALLOW_ONCE,
            decided_by="senior-admin",
            reason="Verified maintenance window",
        )
        return True

    async def execute(self, action: ActionProposal):
        """Final execution of the action with budget and cycle checks."""
        # 5. Check Budget
        if not await self.budget.check_budget(self.session.id, cost=action.weight):
            logger.error("BUDGET EXHAUSTED: Cannot perform more actions.")
            return

        # 6. Cycle & Execution
        await self.guard.acquire_cycle(self.session.id, cycle_id=uuid4())
        try:
            await self.budget.increment(self.session.id, cost=action.weight)

            # Record audit event
            await self.event_store.append(
                session_id=self.session.id,
                event_kind=EventKind.EXECUTION_COMPLETED,
                payload={"action": action.decision, "resource": action.resource_id},
                agent_id=self.agent_id,
                state_bearing=True,
            )

            action.status = ProposalStatus.EXECUTED.value
            await self.uow._session.flush()
            logger.info(f"SUCCESS: Executed {action.decision} on {action.resource_id}")
        finally:
            await self.guard.release_cycle(self.session.id)


async def main():
    register_models()
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(ReferenceBase.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_maker() as session:
        uow = AsyncSqlAlchemyUnitOfWork(session)
        agent = CloudOpsAgent(uow)

        await agent.initialize(create_cloud_policy())

        # 1. Read-only (Always Approved)
        await agent.run_task(ActionName.DESCRIBE_RESOURCES, "region-us-east-1")

        # 2. Reversible Low Risk (Auto Approved)
        await agent.run_task(ActionName.STOP_INSTANCE, "i-0987654321", risk_score=0.95, weight=2.0)

        # 3. Destructive High Risk (Manual Approval Required)
        await agent.run_task(ActionName.TERMINATE_INSTANCE, "i-1234567890", risk_score=0.1, weight=50.0)

        # 4. Blocked Action
        await agent.run_task(ActionName.WIPE_DATABASE, "db-prod-01")

        # 5. Resource Lock Test (Propose same resource again before commit)
        # Note: In this sequential example it won't trigger lock error because we release cycle each time.
        # But we can demonstrate budget pressure.
        await agent.run_task(ActionName.TERMINATE_INSTANCE, "i-9999999999", risk_score=0.1, weight=40.0)

        # 6. Budget Exhaustion
        await agent.run_task(ActionName.TERMINATE_INSTANCE, "i-excessive", risk_score=0.1, weight=20.0)

        await uow.commit()

    await engine.dispose()
    logger.info("\nCloud Ops Example Completed.")


if __name__ == "__main__":
    asyncio.run(main())
