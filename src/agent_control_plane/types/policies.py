"""Policy-related DTOs."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

from .enums import ActionName, ExecutionMode, RiskLevel, parse_action_name


class RiskLimits(BaseModel):
    """Risk limit thresholds for a policy."""

    max_risk_score: Decimal = Decimal("10000")
    max_weight_pct: Decimal = Decimal("5.0")
    custom: dict[str, Decimal] = Field(default_factory=dict)


class AutoApproveConditions(BaseModel):
    """Conditions under which proposals can be auto-approved."""

    max_risk_tier: RiskLevel = RiskLevel.LOW
    dry_run_only: bool = True
    max_weight: Decimal = Decimal("2.5")
    min_score: Decimal = Decimal("0.7")

    @field_validator("max_risk_tier", mode="before")
    @classmethod
    def _parse_risk_tier(cls, value: RiskLevel | str) -> RiskLevel:
        if isinstance(value, RiskLevel):
            return value
        return RiskLevel(value.strip().lower())


class ActionTiers(BaseModel):
    """Action classification tiers."""

    blocked: list[ActionName] = Field(default_factory=list)
    always_approve: list[ActionName] = Field(default_factory=list)
    auto_approve: list[ActionName] = Field(default_factory=list)
    unrestricted: list[ActionName] = Field(default_factory=list)

    @field_validator("blocked", "always_approve", "auto_approve", "unrestricted", mode="before")
    @classmethod
    def _parse_actions(cls, value: list[ActionName | str]) -> list[ActionName]:
        return [parse_action_name(item) for item in value]


class PolicySnapshotDTO(BaseModel):
    """Immutable policy configuration frozen at session start."""

    id: UUID = Field(default_factory=uuid4)
    action_tiers: ActionTiers = Field(default_factory=ActionTiers)
    risk_limits: RiskLimits = Field(default_factory=RiskLimits)
    asset_scope: str | None = None
    execution_mode: ExecutionMode = ExecutionMode.DRY_RUN
    approval_timeout_seconds: int = 3600
    auto_approve_conditions: AutoApproveConditions = Field(default_factory=AutoApproveConditions)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
