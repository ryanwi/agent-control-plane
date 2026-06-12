"""Tests for steering action tier and SteeringContext."""

from decimal import Decimal
from uuid import uuid4

import pytest

from agent_control_plane.engine.action_policy import SteeringActionHandler
from agent_control_plane.engine.policy_engine import PolicyEngine
from agent_control_plane.engine.router import ProposalRouter
from agent_control_plane.types.enums import (
    ActionTier,
    ExecutionMode,
    RiskLevel,
    RoutingResolutionStep,
)
from agent_control_plane.types.policies import PolicySnapshot
from agent_control_plane.types.proposals import ActionProposal
from agent_control_plane.types.steering import SteeringContext


def _policy(**overrides) -> PolicySnapshot:
    defaults = {
        "action_tiers": {
            "blocked": ["ban"],
            "always_approve": ["refund"],
            "auto_approve": ["status"],
            "steer": ["change_address"],
            "unrestricted": ["check_balance"],
        },
        "risk_limits": {"max_risk_score": "10000", "max_weight_pct": "5.0", "custom": {}},
        "execution_mode": ExecutionMode.DRY_RUN,
        "approval_timeout_seconds": 300,
        "auto_approve_conditions": {
            "max_risk_tier": RiskLevel.LOW,
            "dry_run_only": True,
            "max_weight": "2.5",
            "min_score": "0.7",
        },
    }
    defaults.update(overrides)
    return PolicySnapshot(**defaults)


def _proposal(**overrides) -> ActionProposal:
    defaults = {
        "session_id": uuid4(),
        "resource_id": "res-001",
        "resource_type": "task",
        "decision": "change_address",
        "reasoning": "test",
    }
    defaults.update(overrides)
    return ActionProposal(**defaults)


class TestSteeringContext:
    def test_dto_defaults(self):
        ctx = SteeringContext(guidance="Try something else")
        assert ctx.guidance == "Try something else"
        assert ctx.suggested_actions == []
        assert ctx.max_retries == 3
        assert ctx.metadata == {}

    def test_dto_with_all_fields(self):
        ctx = SteeringContext(
            guidance="Use status instead",
            suggested_actions=["status", "check_balance"],
            max_retries=5,
            metadata={"source": "policy"},
        )
        assert len(ctx.suggested_actions) == 2
        assert ctx.max_retries == 5


class TestSteeringActionHandler:
    def test_classify_tier_returns_steer(self):
        handler = SteeringActionHandler()
        policy = _policy()
        proposal = _proposal()
        assert handler.classify_tier(proposal, RiskLevel.LOW, policy, True) == ActionTier.STEER

    def test_build_routing_reason(self):
        handler = SteeringActionHandler()
        proposal = _proposal()
        routing = handler.build_routing_reason(proposal, RiskLevel.LOW, ActionTier.STEER)
        assert "steered" in routing.reason.lower()
        assert routing.resolution_step == RoutingResolutionStep.POLICY_LIST_MATCH

    def test_build_steering_context_includes_suggestions(self):
        handler = SteeringActionHandler()
        policy = _policy()
        proposal = _proposal()
        ctx = handler.build_steering_context(proposal, RiskLevel.LOW, policy)
        assert "status" in ctx.suggested_actions
        assert "check_balance" in ctx.suggested_actions
        assert "change_address" in ctx.guidance.lower()

    def test_build_steering_context_no_alternatives(self):
        policy = _policy(
            action_tiers={
                "blocked": [],
                "always_approve": [],
                "auto_approve": [],
                "steer": ["change_address"],
                "unrestricted": [],
            }
        )
        handler = SteeringActionHandler()
        proposal = _proposal()
        ctx = handler.build_steering_context(proposal, RiskLevel.LOW, policy)
        assert "no pre-approved alternatives" in ctx.guidance.lower()
        assert ctx.suggested_actions == []


class TestPolicyEngineSteer:
    def test_steer_tier_classification(self):
        engine = PolicyEngine(_policy())
        proposal = _proposal(decision="change_address")
        assert engine.classify_action_tier(proposal, RiskLevel.LOW) == ActionTier.STEER

    def test_steer_routing_reason(self):
        engine = PolicyEngine(_policy())
        proposal = _proposal(decision="change_address")
        routing = engine.build_routing_reason(proposal, RiskLevel.LOW, ActionTier.STEER)
        assert "steered" in routing.reason.lower()
        assert routing.resolution_step == RoutingResolutionStep.POLICY_LIST_MATCH

    def test_blocked_takes_precedence_over_steer(self):
        """If an action is in both blocked and steer lists, blocked wins."""
        policy = _policy(
            action_tiers={
                "blocked": ["change_address"],
                "always_approve": [],
                "auto_approve": [],
                "steer": ["change_address"],
                "unrestricted": [],
            }
        )
        engine = PolicyEngine(policy)
        proposal = _proposal(decision="change_address")
        assert engine.classify_action_tier(proposal, RiskLevel.LOW) == ActionTier.BLOCKED


class TestProposalRouterSteer:
    @pytest.mark.asyncio
    async def test_route_steer_populates_steering_context(self):
        router = ProposalRouter(PolicyEngine(_policy()))
        proposal = _proposal(
            decision="change_address",
            weight=Decimal("1.0"),
            score=Decimal("0.9"),
        )
        decision = await router.route(proposal)
        assert decision.tier == ActionTier.STEER
        assert decision.steering is not None
        assert isinstance(decision.steering, SteeringContext)
        assert len(decision.steering.suggested_actions) > 0

    @pytest.mark.asyncio
    async def test_route_non_steer_has_no_steering_context(self):
        router = ProposalRouter(PolicyEngine(_policy()))
        proposal = _proposal(
            decision="status",
            weight=Decimal("1.0"),
            score=Decimal("0.9"),
        )
        decision = await router.route(proposal)
        assert decision.tier == ActionTier.AUTO_APPROVE
        assert decision.steering is None


def _session_state(**overrides):
    from agent_control_plane.types.sessions import SessionState, SessionStatus

    defaults = {
        "id": uuid4(),
        "session_name": "test-session",
        "status": SessionStatus.ACTIVE,
        "execution_mode": ExecutionMode.DRY_RUN,
        "max_cost": Decimal("100"),
        "max_action_count": 50,
        "steering_history": {},
    }
    defaults.update(overrides)
    return SessionState(**defaults)


class TestProposalRouterSteerRecursion:
    @pytest.mark.asyncio
    async def test_route_steer_recursion_escalation(self):
        policy = _policy(max_steering_retries=2)
        router = ProposalRouter(PolicyEngine(policy))

        session = _session_state(steering_history={"change_address": 0})

        proposal = _proposal(
            decision="change_address",
            weight=Decimal("1.0"),
            score=Decimal("0.9"),
        )

        # First route: count goes from 0 to 1, tier should be STEER
        decision1 = await router.route(proposal, session_state=session)
        assert decision1.tier == ActionTier.STEER
        assert session.steering_history["change_address"] == 1

        # Second route: count goes from 1 to 2, tier should be STEER
        decision2 = await router.route(proposal, session_state=session)
        assert decision2.tier == ActionTier.STEER
        assert session.steering_history["change_address"] == 2

        # Third route: count is 2 (equal to max_steering_retries), should escalate!
        decision3 = await router.route(proposal, session_state=session)
        assert decision3.tier == ActionTier.ALWAYS_APPROVE
        assert session.steering_history["change_address"] == 2  # should not increment further
        assert decision3.reason.startswith("Steering limit exceeded")

    @pytest.mark.asyncio
    async def test_route_steer_recursion_event_store(self):
        from agent_control_plane.engine.event_store import EventStore
        from agent_control_plane.types.enums import EventKind
        from tests.fakes import InMemoryEventRepository

        policy = _policy(max_steering_retries=0)
        repo = InMemoryEventRepository()
        event_store = EventStore(repo)

        router = ProposalRouter(
            PolicyEngine(policy),
            event_store=event_store,
        )

        session = _session_state(steering_history={"change_address": 0})

        proposal = _proposal(
            session_id=session.id,
            decision="change_address",
            weight=Decimal("1.0"),
            score=Decimal("0.9"),
        )

        decision = await router.route(proposal, session_state=session)
        assert decision.tier == ActionTier.ALWAYS_APPROVE

        # Verify event was recorded
        events = await event_store.replay(session.id)
        assert len(events) == 1
        assert events[0].kind == EventKind.STEERING_LIMIT_EXCEEDED
        assert events[0].state_bearing is True
        assert events[0].payload["steer_count"] == 0
        assert events[0].payload["max_retries"] == 0
