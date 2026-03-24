"""Tests for steering action tier and SteeringContext."""

from decimal import Decimal
from uuid import uuid4

import pytest

from agent_control_plane.engine.action_policy import SteeringActionHandler
from agent_control_plane.engine.policy_engine import PolicyEngine
from agent_control_plane.engine.router import ProposalRouter
from agent_control_plane.types.enums import (
    ActionName,
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
            "blocked": [ActionName.BAN],
            "always_approve": [ActionName.REFUND],
            "auto_approve": [ActionName.STATUS],
            "steer": [ActionName.CHANGE_ADDRESS],
            "unrestricted": [ActionName.CHECK_BALANCE],
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
        "decision": ActionName.CHANGE_ADDRESS,
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
            suggested_actions=[ActionName.STATUS, ActionName.CHECK_BALANCE],
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
        reason, step = handler.build_routing_reason(proposal, RiskLevel.LOW, ActionTier.STEER)
        assert "steered" in reason.lower()
        assert step == RoutingResolutionStep.POLICY_LIST_MATCH

    def test_build_steering_context_includes_suggestions(self):
        handler = SteeringActionHandler()
        policy = _policy()
        proposal = _proposal()
        ctx = handler.build_steering_context(proposal, RiskLevel.LOW, policy)
        assert ActionName.STATUS in ctx.suggested_actions
        assert ActionName.CHECK_BALANCE in ctx.suggested_actions
        assert "change_address" in ctx.guidance.lower()

    def test_build_steering_context_no_alternatives(self):
        policy = _policy(
            action_tiers={
                "blocked": [],
                "always_approve": [],
                "auto_approve": [],
                "steer": [ActionName.CHANGE_ADDRESS],
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
        proposal = _proposal(decision=ActionName.CHANGE_ADDRESS)
        assert engine.classify_action_tier(proposal, RiskLevel.LOW) == ActionTier.STEER

    def test_steer_routing_reason(self):
        engine = PolicyEngine(_policy())
        proposal = _proposal(decision=ActionName.CHANGE_ADDRESS)
        reason, step = engine.build_routing_reason(proposal, RiskLevel.LOW, ActionTier.STEER)
        assert "steered" in reason.lower()
        assert step == RoutingResolutionStep.POLICY_LIST_MATCH

    def test_blocked_takes_precedence_over_steer(self):
        """If an action is in both blocked and steer lists, blocked wins."""
        policy = _policy(
            action_tiers={
                "blocked": [ActionName.CHANGE_ADDRESS],
                "always_approve": [],
                "auto_approve": [],
                "steer": [ActionName.CHANGE_ADDRESS],
                "unrestricted": [],
            }
        )
        engine = PolicyEngine(policy)
        proposal = _proposal(decision=ActionName.CHANGE_ADDRESS)
        assert engine.classify_action_tier(proposal, RiskLevel.LOW) == ActionTier.BLOCKED


class TestProposalRouterSteer:
    @pytest.mark.asyncio
    async def test_route_steer_populates_steering_context(self):
        router = ProposalRouter(PolicyEngine(_policy()))
        proposal = _proposal(
            decision=ActionName.CHANGE_ADDRESS,
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
            decision=ActionName.STATUS,
            weight=Decimal("1.0"),
            score=Decimal("0.9"),
        )
        decision = await router.route(proposal)
        assert decision.tier == ActionTier.AUTO_APPROVE
        assert decision.steering is None
