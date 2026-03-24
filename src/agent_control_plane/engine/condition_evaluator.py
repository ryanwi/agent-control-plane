"""Engine for evaluating condition trees."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent_control_plane.types.conditions import (
    ActionCondition,
    AndCondition,
    AssetCondition,
    ConditionNode,
    EvaluatorCondition,
    NotCondition,
    OrCondition,
    RiskLevelCondition,
    ScoreCondition,
    WeightCondition,
)
from agent_control_plane.types.enums import RiskLevel
from agent_control_plane.types.policies import PolicySnapshot
from agent_control_plane.types.proposals import ActionProposal

if TYPE_CHECKING:
    from agent_control_plane.evaluators.registry import EvaluatorRegistry


_RISK_OPS = {
    "eq": lambda a, b: a.rank == b.rank,
    "le": lambda a, b: a.rank <= b.rank,
    "ge": lambda a, b: a.rank >= b.rank,
    "lt": lambda a, b: a.rank < b.rank,
    "gt": lambda a, b: a.rank > b.rank,
}


def _evaluate_leaf(
    node: RiskLevelCondition | WeightCondition | ScoreCondition | ActionCondition | AssetCondition,
    proposal: ActionProposal,
    risk_level: RiskLevel,
) -> bool:
    """Evaluate a synchronous leaf condition."""
    if isinstance(node, RiskLevelCondition):
        return bool(_RISK_OPS[node.operator](risk_level, node.level))
    if isinstance(node, WeightCondition):
        return proposal.weight <= node.max_weight
    if isinstance(node, ScoreCondition):
        return proposal.score >= node.min_score
    if isinstance(node, ActionCondition):
        action_str = str(proposal.decision)
        in_list = action_str in {str(a) for a in node.actions}
        return in_list if node.mode == "allow" else not in_list
    # AssetCondition
    upper = proposal.resource_id.upper()
    return any(p.upper() in upper for p in node.patterns)


class ConditionEvaluator:
    """Evaluates a ConditionNode tree against a proposal."""

    def __init__(self, evaluator_registry: EvaluatorRegistry | None = None) -> None:
        self._evaluator_registry = evaluator_registry

    async def evaluate(
        self,
        node: ConditionNode,
        proposal: ActionProposal,
        risk_level: RiskLevel,
        policy: PolicySnapshot,
    ) -> bool:
        """Evaluate a condition node tree. Returns True if the condition is met."""
        if isinstance(node, RiskLevelCondition | WeightCondition | ScoreCondition | ActionCondition | AssetCondition):
            return _evaluate_leaf(node, proposal, risk_level)

        if isinstance(node, EvaluatorCondition):
            return await self._evaluate_evaluator(node, proposal, policy)

        if isinstance(node, AndCondition):
            for child in node.conditions:
                if not await self.evaluate(child, proposal, risk_level, policy):
                    return False
            return True

        if isinstance(node, OrCondition):
            for child in node.conditions:
                if await self.evaluate(child, proposal, risk_level, policy):
                    return True
            return False

        if isinstance(node, NotCondition):
            return not await self.evaluate(node.condition, proposal, risk_level, policy)

        raise TypeError(f"Unknown condition node type: {type(node)}")  # pragma: no cover

    async def _evaluate_evaluator(
        self,
        node: EvaluatorCondition,
        proposal: ActionProposal,
        policy: PolicySnapshot,
    ) -> bool:
        if self._evaluator_registry is None:
            raise ValueError("EvaluatorCondition requires an evaluator_registry")
        evaluator = self._evaluator_registry.get(node.evaluator_name)
        if evaluator is None:
            raise ValueError(f"Unknown evaluator: {node.evaluator_name}")
        result = await evaluator.evaluate(proposal, policy)
        return result.allow
