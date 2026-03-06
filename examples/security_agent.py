"""
Security Agent Example using agent-control-plane.

This example simulates a security monitoring agent that takes automated actions
governed by the control plane.

Actions:
- block_ip: High risk, requires manual approval.
- reset_password: Medium risk, auto-approved if risk score is low.
- log_incident: Low risk, always allowed (auto-approved).

Run:
    uv run python examples/security_agent.py
"""

import asyncio
import logging
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_control_plane import (
    ActionProposal,
    ActionTier,
    ApprovalDecisionType,
    ApprovalGate,
    AsyncSqlAlchemyUnitOfWork,
    BudgetTracker,
    ConcurrencyGuard,
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

# Setup basic logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = "sqlite+aiosqlite:///./security_agent_example.db"


def create_security_policy() -> PolicySnapshotDTO:
    """Define the security governance policy."""
    return PolicySnapshotDTO(
        action_tiers={
            "blocked": ["delete_all_logs"],  # Strictly forbidden
            "always_approve": ["log_incident"],  # Low risk, always allowed
            "auto_approve": ["reset_password"],  # Can be auto-approved based on conditions
            "unrestricted": [],
        },
        risk_limits={
            "max_risk_score": "100",
            "max_weight_pct": "10.0",
            "custom": {},
        },
        execution_mode="live",
        approval_timeout_seconds=60,
        auto_approve_conditions={
            "max_risk_tier": "low",
            "dry_run_only": False,
            "max_weight": "5.0",
            "min_score": "0.8",
        },
    )


class SecurityAgent:
    def __init__(self, uow: AsyncSqlAlchemyUnitOfWork, agent_id: str = "security-bot-01"):
        self.uow = uow
        self.agent_id = agent_id

        # Initialize engines
        self.session_manager = SessionManager(uow.session_repo)
        self.event_store = EventStore(uow.event_repo)
        self.approval_gate = ApprovalGate(self.event_store, uow.approval_repo, uow.proposal_repo)
        self.budget = BudgetTracker(uow.session_repo)
        self.guard = ConcurrencyGuard(uow.session_repo, uow.proposal_repo)

    async def setup_session(self, policy_snapshot: PolicySnapshotDTO):
        """Register policy and create a monitoring session."""
        policy_id = await self.session_manager.create_policy(
            action_tiers=policy_snapshot.action_tiers.model_dump(mode="json"),
            risk_limits=policy_snapshot.risk_limits.model_dump(mode="json"),
            asset_scope=policy_snapshot.asset_scope,
            execution_mode=policy_snapshot.execution_mode.value,
            approval_timeout_seconds=policy_snapshot.approval_timeout_seconds,
            auto_approve_conditions=policy_snapshot.auto_approve_conditions.model_dump(mode="json"),
        )
        self.session = await self.session_manager.create_session(
            session_name=f"security-monitor-{uuid4().hex[:8]}",
            execution_mode=policy_snapshot.execution_mode.value,
            max_cost=Decimal("50.0"),  # Total risk budget for this session
            max_action_count=20,
            policy_id=policy_id,
        )
        self.router = ProposalRouter(PolicyEngine(policy_snapshot))
        logger.info(f"Session {self.session.id} initialized for agent {self.agent_id}")

    async def propose_action(
        self,
        resource_id: str,
        resource_type: str,
        action_name: str,
        reason: str,
        risk_score: float = 0.5,
        weight: float = 1.0,
    ):
        """Propose an action and handle the control flow."""
        proposal_dto = ActionProposalDTO(
            session_id=self.session.id,
            resource_id=resource_id,
            resource_type=resource_type,
            decision=action_name,
            reasoning=reason,
            metadata={"agent": self.agent_id},
            weight=Decimal(str(weight)),
            score=Decimal(str(risk_score)),
        )

        # 1. Route the proposal
        route = self.router.route(proposal_dto)
        logger.info(f"Proposed '{action_name}' on {resource_type}:{resource_id}. Tier: {route.tier}")

        if route.tier == ActionTier.BLOCKED:
            logger.warning(f"Action {action_name} is BLOCKED by policy: {route.reason}")
            return

        # 2. Check for resource locks (prevent concurrent conflicting actions)
        try:
            await self.guard.check_resource_lock(self.session.id, resource_id)
        except Exception as e:
            logger.error(f"Resource {resource_id} is locked: {e}")
            return

        # 3. Persist the proposal
        action = ActionProposal(
            session_id=proposal_dto.session_id,
            resource_id=proposal_dto.resource_id,
            resource_type=proposal_dto.resource_type,
            decision=proposal_dto.decision,
            reasoning=proposal_dto.reasoning,
            metadata_json=proposal_dto.metadata,
            weight=proposal_dto.weight,
            score=proposal_dto.score,
            action_tier=route.tier.value,
            risk_level=route.risk_level.value,
            status=ProposalStatus.PENDING.value,
        )
        self.uow._session.add(action)
        await self.uow._session.flush()

        # 4. Handle based on tier
        if route.tier == ActionTier.ALWAYS_APPROVE or (
            route.tier == ActionTier.AUTO_APPROVE and route.risk_level.value == "LOW"
        ):
            # Auto-approvable
            logger.info(f"Action {action_name} auto-approved.")
            await self.execute_action(action, route)
        else:
            # Requires approval ticket
            logger.info(f"Action {action_name} requires approval. Creating ticket...")
            ticket = await self.approval_gate.create_ticket(self.session.id, action.id)
            # In a real scenario, this would wait for a human. Here we simulate an operator decision.
            await self.simulate_operator_decision(ticket.id, action)

    async def simulate_operator_decision(self, ticket_id: str, action: ActionProposal):
        """Simulate a human operator reviewing the ticket."""
        logger.info(f"Operator reviewing ticket {ticket_id} for {action.decision}...")

        # Simulate approval for everything except if we've already done it too much
        await self.approval_gate.approve(
            ticket_id,
            decision_type=ApprovalDecisionType.ALLOW_FOR_SESSION,
            decided_by="security-ops-team",
            scope_resource_ids=[action.resource_id],
        )

        # Check if we are now scoped
        scoped = await self.approval_gate.check_session_scope(
            session_id=self.session.id,
            resource_id=action.resource_id,
            cost=action.weight,
        )
        if scoped:
            logger.info(f"Operator APPROVED {action.decision}")
            # Re-fetch or use route from somewhere? We'll just assume it's okay now.
            # In a real agent, the execution loop would pick up approved tickets.
            await self.execute_action(action, None)  # None for route as we already passed policy
        else:
            logger.warning(f"Operator DENIED or scope mismatch for {action.decision}")

    async def execute_action(self, action: ActionProposal, route):
        """Final check and execution of the action."""
        # 5. Check Budget
        if not await self.budget.check_budget(self.session.id, cost=action.weight, action_count=1):
            logger.error(f"Budget exceeded for session {self.session.id}!")
            return

        # 6. Acquire cycle and execute
        await self.guard.acquire_cycle(self.session.id, cycle_id=uuid4())
        try:
            # Increment budget consumption
            await self.budget.increment(self.session.id, cost=action.weight, action_count=1)

            # Record event
            await self.event_store.append(
                session_id=self.session.id,
                event_kind="action.executed",
                payload={
                    "action": action.decision,
                    "resource_id": action.resource_id,
                    "resource_type": action.resource_type,
                },
                state_bearing=True,
                agent_id=self.agent_id,
                correlation_id=uuid4(),
            )

            action.status = ProposalStatus.EXECUTED.value
            await self.uow._session.flush()
            logger.info(f"Successfully EXECUTED: {action.decision} on {action.resource_id}")

        finally:
            await self.guard.release_cycle(self.session.id)


async def main():
    Path("./security_agent_example.db").unlink(missing_ok=True)

    register_models()
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(ReferenceBase.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)

    async with session_maker() as session:
        uow = AsyncSqlAlchemyUnitOfWork(session)
        agent = SecurityAgent(uow)
        policy = create_security_policy()

        await agent.setup_session(policy)

        # Scenario 1: Always approve (low risk)
        await agent.propose_action(
            resource_id="login-log-001",
            resource_type="audit_log",
            action_name="log_incident",
            reason="Detected multiple failed login attempts from IP 1.2.3.4",
            risk_score=0.1,
            weight=0.1,
        )

        # Scenario 2: Auto-approve (medium risk but under threshold)
        await agent.propose_action(
            resource_id="user-ryan",
            resource_type="user_account",
            action_name="reset_password",
            reason="Suspicious login patterns, resetting password as precaution",
            risk_score=0.75,  # High score but we'll see how policy handles it
            weight=1.0,
        )

        # Scenario 3: Manual approval (not in auto_approve list, so it goes to
        # UNRESTRICTED/MANUAL). block_ip is not in any tier, so it falls through.
        await agent.propose_action(
            resource_id="ip-1.2.3.4",
            resource_type="firewall_rule",
            action_name="block_ip",
            reason="IP 1.2.3.4 confirmed brute force source",
            risk_score=0.9,
            weight=5.0,
        )

        # Scenario 4: Blocked action
        await agent.propose_action(
            resource_id="system-logs",
            resource_type="storage",
            action_name="delete_all_logs",
            reason="Cleaning up space",
            risk_score=1.0,
            weight=100.0,
        )

        # Scenario 5: Budget exhaustion
        logger.info("--- Testing Budget Exhaustion ---")
        for i in range(10):
            await agent.propose_action(
                resource_id=f"ip-10.0.0.{i}",
                resource_type="firewall_rule",
                action_name="block_ip",
                reason="Brute force attack",
                risk_score=0.9,
                weight=5.0,  # Total 11 * 5.0 = 55.0 > 50.0
            )

        await uow.commit()

    await engine.dispose()
    logger.info("Example completed.")


if __name__ == "__main__":
    asyncio.run(main())
