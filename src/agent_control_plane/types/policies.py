"""Policy-related DTOs."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

from .aliases import AliasProfiledModel
from .enums import ActionValue, AssetScope, ExecutionMode, RiskLevel, parse_action_name
from .extensions import get_risk_limits_extension_schema


class RiskLimits(AliasProfiledModel):
    """Risk limit thresholds for a policy."""

    max_risk_score: Decimal = Decimal("10000")
    max_weight_pct: Decimal = Decimal("5.0")
    custom: dict[str, Decimal] = Field(default_factory=dict)

    def validate_extension(self) -> None:
        schema = get_risk_limits_extension_schema()
        if schema is None:
            raise ValueError("No RiskLimits extension schema registered")
        schema.model_validate(self.custom)

    def extension_as(self, schema: type[BaseModel] | None = None) -> BaseModel:
        resolved_schema = schema or get_risk_limits_extension_schema()
        if resolved_schema is None:
            raise ValueError("No RiskLimits extension schema registered")
        return resolved_schema.model_validate(self.custom)


class AutoApproveConditions(AliasProfiledModel):
    """Conditions under which proposals can be auto-approved."""

    max_risk_tier: RiskLevel = RiskLevel.LOW
    dry_run_only: bool = True
    max_weight: Decimal = Decimal("2.5")
    min_score: Decimal = Decimal("0.7")
    condition_tree: Any | None = None

    @field_validator("max_risk_tier", mode="before")
    @classmethod
    def _parse_risk_tier(cls, value: RiskLevel | str) -> RiskLevel:
        if isinstance(value, RiskLevel):
            return value
        return RiskLevel(value.strip().lower())


class ActionTiers(AliasProfiledModel):
    """Action classification tiers."""

    blocked: list[ActionValue] = Field(default_factory=list)
    always_approve: list[ActionValue] = Field(default_factory=list)
    auto_approve: list[ActionValue] = Field(default_factory=list)
    steer: list[ActionValue] = Field(default_factory=list)
    unrestricted: list[ActionValue] = Field(default_factory=list)

    @field_validator("blocked", "always_approve", "auto_approve", "steer", "unrestricted", mode="before")
    @classmethod
    def _parse_actions(cls, value: list[ActionValue]) -> list[ActionValue]:
        return [parse_action_name(item) for item in value]


class PolicySnapshot(AliasProfiledModel):
    """Immutable policy configuration frozen at session start."""

    id: UUID = Field(default_factory=uuid4)
    action_tiers: ActionTiers = Field(default_factory=ActionTiers)
    risk_limits: RiskLimits = Field(default_factory=RiskLimits)
    asset_scope: AssetScope | None = None
    execution_mode: ExecutionMode = ExecutionMode.DRY_RUN
    approval_timeout_seconds: int = 3600
    auto_approve_conditions: AutoApproveConditions = Field(default_factory=AutoApproveConditions)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("asset_scope", mode="before")
    @classmethod
    def _parse_asset_scope(cls, value: AssetScope | str | None) -> AssetScope | None:
        if value is None or isinstance(value, AssetScope):
            return value
        return AssetScope(value.strip().lower())
