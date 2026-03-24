"""Tests for composite condition trees and ConditionEvaluator."""

from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from agent_control_plane.engine.condition_evaluator import ConditionEvaluator
from agent_control_plane.evaluators import (
    EvaluatorRegistry,
    RegexEvaluator,
    RegexEvaluatorConfig,
)
from agent_control_plane.types.conditions import (
    ActionCondition,
    AndCondition,
    AssetCondition,
    EvaluatorCondition,
    NotCondition,
    OrCondition,
    RiskLevelCondition,
    ScoreCondition,
    WeightCondition,
)
from agent_control_plane.types.enums import ActionName, ExecutionMode, RiskLevel
from agent_control_plane.types.policies import PolicySnapshot
from agent_control_plane.types.proposals import ActionProposal


def _policy() -> PolicySnapshot:
    return PolicySnapshot(
        action_tiers={"blocked": [], "always_approve": [], "auto_approve": [], "unrestricted": []},
        execution_mode=ExecutionMode.DRY_RUN,
    )


def _proposal(**overrides) -> ActionProposal:
    defaults = {
        "session_id": uuid4(),
        "resource_id": "res-001",
        "resource_type": "task",
        "decision": ActionName.STATUS,
        "reasoning": "test",
        "weight": Decimal("1.0"),
        "score": Decimal("0.9"),
    }
    defaults.update(overrides)
    return ActionProposal(**defaults)


class TestRiskLevelCondition:
    @pytest.mark.asyncio
    async def test_le_true(self):
        node = RiskLevelCondition(level=RiskLevel.LOW, operator="le")
        ev = ConditionEvaluator()
        assert await ev.evaluate(node, _proposal(), RiskLevel.LOW, _policy()) is True

    @pytest.mark.asyncio
    async def test_le_false(self):
        node = RiskLevelCondition(level=RiskLevel.LOW, operator="le")
        ev = ConditionEvaluator()
        assert await ev.evaluate(node, _proposal(), RiskLevel.MEDIUM, _policy()) is False

    @pytest.mark.asyncio
    async def test_eq(self):
        node = RiskLevelCondition(level=RiskLevel.MEDIUM, operator="eq")
        ev = ConditionEvaluator()
        assert await ev.evaluate(node, _proposal(), RiskLevel.MEDIUM, _policy()) is True
        assert await ev.evaluate(node, _proposal(), RiskLevel.LOW, _policy()) is False

    @pytest.mark.asyncio
    async def test_gt(self):
        node = RiskLevelCondition(level=RiskLevel.LOW, operator="gt")
        ev = ConditionEvaluator()
        assert await ev.evaluate(node, _proposal(), RiskLevel.MEDIUM, _policy()) is True
        assert await ev.evaluate(node, _proposal(), RiskLevel.LOW, _policy()) is False


class TestWeightCondition:
    @pytest.mark.asyncio
    async def test_within_threshold(self):
        node = WeightCondition(max_weight=Decimal("2.0"))
        ev = ConditionEvaluator()
        assert await ev.evaluate(node, _proposal(weight=Decimal("1.5")), RiskLevel.LOW, _policy()) is True

    @pytest.mark.asyncio
    async def test_exceeds_threshold(self):
        node = WeightCondition(max_weight=Decimal("1.0"))
        ev = ConditionEvaluator()
        assert await ev.evaluate(node, _proposal(weight=Decimal("1.5")), RiskLevel.LOW, _policy()) is False


class TestScoreCondition:
    @pytest.mark.asyncio
    async def test_above_min(self):
        node = ScoreCondition(min_score=Decimal("0.5"))
        ev = ConditionEvaluator()
        assert await ev.evaluate(node, _proposal(score=Decimal("0.8")), RiskLevel.LOW, _policy()) is True

    @pytest.mark.asyncio
    async def test_below_min(self):
        node = ScoreCondition(min_score=Decimal("0.9"))
        ev = ConditionEvaluator()
        assert await ev.evaluate(node, _proposal(score=Decimal("0.5")), RiskLevel.LOW, _policy()) is False


class TestActionCondition:
    @pytest.mark.asyncio
    async def test_allow_mode_in_list(self):
        node = ActionCondition(actions=[ActionName.STATUS], mode="allow")
        ev = ConditionEvaluator()
        assert await ev.evaluate(node, _proposal(decision=ActionName.STATUS), RiskLevel.LOW, _policy()) is True

    @pytest.mark.asyncio
    async def test_allow_mode_not_in_list(self):
        node = ActionCondition(actions=[ActionName.REFUND], mode="allow")
        ev = ConditionEvaluator()
        assert await ev.evaluate(node, _proposal(decision=ActionName.STATUS), RiskLevel.LOW, _policy()) is False

    @pytest.mark.asyncio
    async def test_deny_mode_in_list(self):
        node = ActionCondition(actions=[ActionName.BAN], mode="deny")
        ev = ConditionEvaluator()
        assert await ev.evaluate(node, _proposal(decision=ActionName.BAN), RiskLevel.LOW, _policy()) is False

    @pytest.mark.asyncio
    async def test_deny_mode_not_in_list(self):
        node = ActionCondition(actions=[ActionName.BAN], mode="deny")
        ev = ConditionEvaluator()
        assert await ev.evaluate(node, _proposal(decision=ActionName.STATUS), RiskLevel.LOW, _policy()) is True


class TestAssetCondition:
    @pytest.mark.asyncio
    async def test_pattern_match(self):
        node = AssetCondition(patterns=["RES"])
        ev = ConditionEvaluator()
        assert await ev.evaluate(node, _proposal(resource_id="res-001"), RiskLevel.LOW, _policy()) is True

    @pytest.mark.asyncio
    async def test_no_match(self):
        node = AssetCondition(patterns=["VIP"])
        ev = ConditionEvaluator()
        assert await ev.evaluate(node, _proposal(resource_id="res-001"), RiskLevel.LOW, _policy()) is False


class TestAndCondition:
    @pytest.mark.asyncio
    async def test_all_true(self):
        node = AndCondition(
            conditions=[
                RiskLevelCondition(level=RiskLevel.LOW, operator="le"),
                ScoreCondition(min_score=Decimal("0.5")),
            ]
        )
        ev = ConditionEvaluator()
        assert await ev.evaluate(node, _proposal(), RiskLevel.LOW, _policy()) is True

    @pytest.mark.asyncio
    async def test_short_circuits_on_false(self):
        node = AndCondition(
            conditions=[
                ScoreCondition(min_score=Decimal("0.99")),  # False
                RiskLevelCondition(level=RiskLevel.LOW, operator="le"),
            ]
        )
        ev = ConditionEvaluator()
        assert await ev.evaluate(node, _proposal(score=Decimal("0.5")), RiskLevel.LOW, _policy()) is False


class TestOrCondition:
    @pytest.mark.asyncio
    async def test_first_true(self):
        node = OrCondition(
            conditions=[
                RiskLevelCondition(level=RiskLevel.LOW, operator="le"),
                ScoreCondition(min_score=Decimal("0.99")),
            ]
        )
        ev = ConditionEvaluator()
        assert await ev.evaluate(node, _proposal(), RiskLevel.LOW, _policy()) is True

    @pytest.mark.asyncio
    async def test_all_false(self):
        node = OrCondition(
            conditions=[
                ScoreCondition(min_score=Decimal("0.99")),
                WeightCondition(max_weight=Decimal("0.1")),
            ]
        )
        ev = ConditionEvaluator()
        proposal = _proposal(score=Decimal("0.5"), weight=Decimal("1.0"))
        assert await ev.evaluate(node, proposal, RiskLevel.LOW, _policy()) is False


class TestNotCondition:
    @pytest.mark.asyncio
    async def test_inverts_true(self):
        node = NotCondition(condition=RiskLevelCondition(level=RiskLevel.LOW, operator="le"))
        ev = ConditionEvaluator()
        assert await ev.evaluate(node, _proposal(), RiskLevel.LOW, _policy()) is False

    @pytest.mark.asyncio
    async def test_inverts_false(self):
        node = NotCondition(condition=ScoreCondition(min_score=Decimal("0.99")))
        ev = ConditionEvaluator()
        assert await ev.evaluate(node, _proposal(score=Decimal("0.5")), RiskLevel.LOW, _policy()) is True


class TestNestedTree:
    @pytest.mark.asyncio
    async def test_complex_tree(self):
        """and(or(risk<=low, score>=0.9), not(action=ban))"""
        node = AndCondition(
            conditions=[
                OrCondition(
                    conditions=[
                        RiskLevelCondition(level=RiskLevel.LOW, operator="le"),
                        ScoreCondition(min_score=Decimal("0.9")),
                    ]
                ),
                NotCondition(condition=ActionCondition(actions=[ActionName.BAN], mode="allow")),
            ]
        )
        ev = ConditionEvaluator()
        # Low risk, not ban → True
        assert await ev.evaluate(node, _proposal(decision=ActionName.STATUS), RiskLevel.LOW, _policy()) is True
        # Ban action → False (not condition fails)
        assert await ev.evaluate(node, _proposal(decision=ActionName.BAN), RiskLevel.LOW, _policy()) is False


class TestMaxDepth:
    def test_exceeds_max_depth_raises(self):
        """Build a tree exceeding 6 levels deep and expect ValidationError."""
        inner = RiskLevelCondition(level=RiskLevel.LOW)
        # 6 levels is okay
        for _ in range(6):
            inner = AndCondition(conditions=[inner])  # type: ignore[assignment]
        # 7th level should raise
        with pytest.raises(ValidationError):
            AndCondition(conditions=[inner])  # type: ignore[arg-type]


class TestEvaluatorCondition:
    @pytest.mark.asyncio
    async def test_integration_with_registry(self):
        registry = EvaluatorRegistry(auto_discover=False)
        regex_ev = RegexEvaluator(RegexEvaluatorConfig(patterns=[r"^res"], field="resource_id", deny_on_match=True))
        registry.register(regex_ev)

        node = EvaluatorCondition(evaluator_name="regex")
        ev = ConditionEvaluator(evaluator_registry=registry)
        # "res-001" matches "^res" with deny_on_match=True → allow=False
        assert await ev.evaluate(node, _proposal(), RiskLevel.LOW, _policy()) is False

    @pytest.mark.asyncio
    async def test_unknown_evaluator_raises(self):
        registry = EvaluatorRegistry(auto_discover=False)
        node = EvaluatorCondition(evaluator_name="nonexistent")
        ev = ConditionEvaluator(evaluator_registry=registry)
        with pytest.raises(ValueError, match="Unknown evaluator"):
            await ev.evaluate(node, _proposal(), RiskLevel.LOW, _policy())

    @pytest.mark.asyncio
    async def test_no_registry_raises(self):
        node = EvaluatorCondition(evaluator_name="regex")
        ev = ConditionEvaluator()
        with pytest.raises(ValueError, match="requires an evaluator_registry"):
            await ev.evaluate(node, _proposal(), RiskLevel.LOW, _policy())


class TestConditionTreeOnPolicy:
    @pytest.mark.asyncio
    async def test_policy_engine_with_condition_tree(self):
        from agent_control_plane.engine.policy_engine import PolicyEngine

        tree = AndCondition(
            conditions=[
                RiskLevelCondition(level=RiskLevel.LOW, operator="le"),
                ScoreCondition(min_score=Decimal("0.7")),
            ]
        )
        policy = PolicySnapshot(
            action_tiers={"blocked": [], "always_approve": [], "auto_approve": [ActionName.STATUS], "unrestricted": []},
            execution_mode=ExecutionMode.DRY_RUN,
            auto_approve_conditions={
                "max_risk_tier": "low",
                "dry_run_only": True,
                "max_weight": "2.5",
                "min_score": "0.7",
                "condition_tree": tree,
            },
        )
        ce = ConditionEvaluator()
        engine = PolicyEngine(policy, condition_evaluator=ce)

        proposal = _proposal(weight=Decimal("1.0"), score=Decimal("0.9"))
        assert await engine.can_auto_approve_with_tree(proposal, RiskLevel.LOW) is True
        assert await engine.can_auto_approve_with_tree(proposal, RiskLevel.MEDIUM) is False
