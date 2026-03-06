"""Tests for PolicyEngine, DefaultRiskClassifier, and ProposalRouter."""

from decimal import Decimal
from uuid import uuid4

import pytest

from agent_control_plane.engine.policy_engine import (
    DefaultAssetClassifier,
    DefaultRiskClassifier,
    PolicyEngine,
)
from agent_control_plane.engine.router import ProposalRouter
from agent_control_plane.types.enums import (
    ActionName,
    ActionTier,
    AssetScope,
    ExecutionMode,
    RiskLevel,
    RoutingResolutionStep,
)
from agent_control_plane.types.policies import PolicySnapshotDTO
from agent_control_plane.types.proposals import ActionProposalDTO


def _policy(**overrides) -> PolicySnapshotDTO:
    defaults = {
        "action_tiers": {
            "blocked": [ActionName.BAN],
            "always_approve": [ActionName.REFUND],
            "auto_approve": [ActionName.STATUS],
            "unrestricted": [],
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
    return PolicySnapshotDTO(**defaults)


def _proposal(**overrides) -> ActionProposalDTO:
    defaults = {
        "session_id": uuid4(),
        "resource_id": "res-001",
        "resource_type": "task",
        "decision": ActionName.REFUND,
        "reasoning": "test",
    }
    defaults.update(overrides)
    return ActionProposalDTO(**defaults)


# ---- DefaultRiskClassifier ----


class TestDefaultRiskClassifier:
    def test_low_risk_when_matched_and_within_thresholds(self):
        policy = _policy()
        proposal = _proposal(weight=Decimal("1.0"), score=Decimal("0.9"))
        classifier = DefaultRiskClassifier()
        assert classifier.classify(proposal, policy) == RiskLevel.LOW

    def test_high_risk_when_weight_exceeds_max(self):
        policy = _policy()
        proposal = _proposal(weight=Decimal("6.0"), score=Decimal("0.9"))
        classifier = DefaultRiskClassifier()
        assert classifier.classify(proposal, policy) == RiskLevel.HIGH

    def test_high_risk_when_score_below_threshold(self):
        policy = _policy()
        proposal = _proposal(weight=Decimal("1.0"), score=Decimal("0.3"))
        classifier = DefaultRiskClassifier()
        assert classifier.classify(proposal, policy) == RiskLevel.HIGH

    def test_medium_risk_when_weight_above_auto_but_below_max(self):
        policy = _policy()
        proposal = _proposal(weight=Decimal("3.0"), score=Decimal("0.8"))
        classifier = DefaultRiskClassifier()
        assert classifier.classify(proposal, policy) == RiskLevel.MEDIUM

    def test_unmatched_asset_not_low(self):
        ac = DefaultAssetClassifier(frozenset({"SPECIAL"}))
        classifier = DefaultRiskClassifier(asset_classifier=ac)
        policy = _policy()
        proposal = _proposal(resource_id="ordinary-thing", weight=Decimal("1.0"), score=Decimal("0.9"))
        # Would be LOW if matched, but classifier says unmatched
        assert classifier.classify(proposal, policy) != RiskLevel.LOW

    def test_custom_risk_classifier_protocol(self):
        class AlwaysHigh:
            def classify(self, proposal, policy):
                return RiskLevel.HIGH

        policy = _policy()
        engine = PolicyEngine(policy, risk_classifier=AlwaysHigh())
        proposal = _proposal(weight=Decimal("0.1"), score=Decimal("0.99"))
        assert engine.classify_risk_level(proposal) == RiskLevel.HIGH


# ---- PolicyEngine ----


class TestPolicyEngine:
    def test_decision_parses_to_enum(self):
        proposal = _proposal(decision="refund")
        assert proposal.decision == ActionName.REFUND

    def test_policy_action_tiers_parse_to_enums(self):
        policy = _policy(
            action_tiers={
                "blocked": ["ban"],
                "always_approve": [],
                "auto_approve": [],
                "unrestricted": [],
            }
        )
        assert policy.action_tiers.blocked == [ActionName.BAN]

    def test_blocked_action(self):
        engine = PolicyEngine(_policy())
        proposal = _proposal(decision=ActionName.BAN)
        assert engine.classify_action_tier(proposal, RiskLevel.LOW) == ActionTier.BLOCKED

    def test_blocked_action_case_insensitive(self):
        engine = PolicyEngine(_policy())
        proposal = _proposal(decision="BAN")
        assert engine.classify_action_tier(proposal, RiskLevel.LOW) == ActionTier.BLOCKED

    def test_unknown_action_fails_closed(self):
        engine = PolicyEngine(_policy())
        proposal = _proposal(decision="totally_new_action")
        assert proposal.decision == ActionName.UNKNOWN
        assert engine.classify_action_tier(proposal, RiskLevel.LOW) == ActionTier.BLOCKED

    def test_exact_matching_no_substring_block(self):
        engine = PolicyEngine(_policy())
        proposal = _proposal(decision=ActionName.UNBAN)
        assert proposal.decision == ActionName.UNBAN
        assert engine.classify_action_tier(proposal, RiskLevel.LOW) == ActionTier.AUTO_APPROVE

    def test_auto_approve_low_risk_dry_run(self):
        engine = PolicyEngine(_policy())
        proposal = _proposal(decision=ActionName.STATUS)
        assert engine.classify_action_tier(proposal, RiskLevel.LOW) == ActionTier.AUTO_APPROVE

    def test_no_auto_approve_in_live_mode_when_dry_run_only(self):
        policy = _policy(execution_mode=ExecutionMode.LIVE)
        engine = PolicyEngine(policy)
        proposal = _proposal(decision=ActionName.STATUS)
        assert engine.classify_action_tier(proposal, RiskLevel.LOW) == ActionTier.ALWAYS_APPROVE

    def test_medium_risk_always_approve(self):
        engine = PolicyEngine(_policy())
        proposal = _proposal(decision=ActionName.REFUND)
        assert engine.classify_action_tier(proposal, RiskLevel.MEDIUM) == ActionTier.ALWAYS_APPROVE

    def test_high_risk_always_approve(self):
        engine = PolicyEngine(_policy())
        proposal = _proposal(decision=ActionName.REFUND)
        assert engine.classify_action_tier(proposal, RiskLevel.HIGH) == ActionTier.ALWAYS_APPROVE

    def test_asset_scope_blocks_unmatched(self):
        ac = DefaultAssetClassifier(frozenset({"VIP"}))
        policy = _policy(asset_scope=AssetScope.MATCHED_ONLY)
        engine = PolicyEngine(policy, asset_classifier=ac)
        proposal = _proposal(resource_id="regular-user")
        assert engine.classify_action_tier(proposal, RiskLevel.LOW) == ActionTier.BLOCKED

    def test_asset_scope_passes_matched(self):
        ac = DefaultAssetClassifier(frozenset({"VIP"}))
        policy = _policy(asset_scope=AssetScope.MATCHED_ONLY)
        engine = PolicyEngine(policy, asset_classifier=ac)
        proposal = _proposal(resource_id="VIP-customer-42", decision="status")
        assert engine.classify_action_tier(proposal, RiskLevel.LOW) == ActionTier.AUTO_APPROVE

    def test_no_asset_scope_passes_all(self):
        engine = PolicyEngine(_policy())
        proposal = _proposal(decision="status")
        assert engine.classify_action_tier(proposal, RiskLevel.LOW) == ActionTier.AUTO_APPROVE


# ---- ProposalRouter ----


class TestProposalRouter:
    @pytest.mark.asyncio
    async def test_route_blocked(self):
        router = ProposalRouter(PolicyEngine(_policy()))
        proposal = _proposal(decision=ActionName.BAN)
        decision = await router.route(proposal)
        assert decision.tier == ActionTier.BLOCKED
        assert decision.resolution_step == RoutingResolutionStep.EXPLICIT_ASSIGNMENT

    @pytest.mark.asyncio
    async def test_route_auto_approve(self):
        router = ProposalRouter(PolicyEngine(_policy()))
        proposal = _proposal(decision=ActionName.STATUS, weight=Decimal("1.0"), score=Decimal("0.9"))
        decision = await router.route(proposal)
        assert decision.tier == ActionTier.AUTO_APPROVE
        assert decision.risk_level == RiskLevel.LOW
        assert decision.resolution_step == RoutingResolutionStep.POLICY_LIST_MATCH

    @pytest.mark.asyncio
    async def test_route_always_approve_medium(self):
        router = ProposalRouter(PolicyEngine(_policy()))
        proposal = _proposal(decision=ActionName.REFUND, weight=Decimal("3.0"), score=Decimal("0.8"))
        decision = await router.route(proposal)
        assert decision.tier == ActionTier.ALWAYS_APPROVE
        assert decision.risk_level == RiskLevel.MEDIUM

    @pytest.mark.asyncio
    async def test_route_always_approve_high(self):
        router = ProposalRouter(PolicyEngine(_policy()))
        proposal = _proposal(decision=ActionName.REFUND, weight=Decimal("6.0"), score=Decimal("0.9"))
        decision = await router.route(proposal)
        assert decision.tier == ActionTier.ALWAYS_APPROVE
        assert decision.risk_level == RiskLevel.HIGH
