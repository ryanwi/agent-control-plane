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
