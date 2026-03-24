"""Recursive boolean condition trees for policy rule composition."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Discriminator, Field, Tag, model_validator

from .enums import ActionValue, RiskLevel


def _check_depth(node: object, current: int = 0, max_depth: int = 6) -> int:
    """Walk a condition tree and return the max depth. Raises ValueError if exceeded."""
    if current > max_depth:
        raise ValueError(f"Condition tree exceeds max depth of {max_depth}")
    if isinstance(node, AndCondition | OrCondition):
        return max((_check_depth(c, current + 1, max_depth) for c in node.conditions), default=current)
    if isinstance(node, NotCondition):
        return _check_depth(node.condition, current + 1, max_depth)
    return current


# ── Leaf conditions ──────────────────────────────────────────────


class RiskLevelCondition(BaseModel):
    """Compare proposal risk level."""

    type: Literal["risk_level"] = "risk_level"
    level: RiskLevel
    operator: Literal["eq", "le", "ge", "lt", "gt"] = "le"


class WeightCondition(BaseModel):
    """Check proposal weight against a threshold."""

    type: Literal["weight"] = "weight"
    max_weight: Decimal


class ScoreCondition(BaseModel):
    """Check proposal score against a minimum threshold."""

    type: Literal["score"] = "score"
    min_score: Decimal


class ActionCondition(BaseModel):
    """Check if proposal action is in a set."""

    type: Literal["action"] = "action"
    actions: list[ActionValue]
    mode: Literal["allow", "deny"] = "allow"


class AssetCondition(BaseModel):
    """Check if resource ID matches any pattern."""

    type: Literal["asset"] = "asset"
    patterns: list[str]


class EvaluatorCondition(BaseModel):
    """Delegate to a named evaluator from the registry."""

    type: Literal["evaluator"] = "evaluator"
    evaluator_name: str
    config: dict[str, Any] = Field(default_factory=dict)


# ── Composite conditions ─────────────────────────────────────────


def _get_type(v: Any) -> str:
    if isinstance(v, dict):
        return str(v.get("type", ""))
    return str(getattr(v, "type", ""))


class AndCondition(BaseModel):
    """All child conditions must be true."""

    type: Literal["and"] = "and"
    conditions: list[ConditionNode]

    @model_validator(mode="after")
    def _validate_depth(self) -> AndCondition:
        _check_depth(self)
        return self


class OrCondition(BaseModel):
    """At least one child condition must be true."""

    type: Literal["or"] = "or"
    conditions: list[ConditionNode]

    @model_validator(mode="after")
    def _validate_depth(self) -> OrCondition:
        _check_depth(self)
        return self


class NotCondition(BaseModel):
    """Negates the child condition."""

    type: Literal["not"] = "not"
    condition: ConditionNode


ConditionNode = Annotated[
    Annotated[RiskLevelCondition, Tag("risk_level")]
    | Annotated[WeightCondition, Tag("weight")]
    | Annotated[ScoreCondition, Tag("score")]
    | Annotated[ActionCondition, Tag("action")]
    | Annotated[AssetCondition, Tag("asset")]
    | Annotated[EvaluatorCondition, Tag("evaluator")]
    | Annotated[AndCondition, Tag("and")]
    | Annotated[OrCondition, Tag("or")]
    | Annotated[NotCondition, Tag("not")],
    Discriminator(_get_type),
]

# Rebuild models with forward references resolved
AndCondition.model_rebuild()
OrCondition.model_rebuild()
NotCondition.model_rebuild()
