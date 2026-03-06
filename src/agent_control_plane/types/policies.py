"""Policy-related DTOs."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from .enums import ExecutionMode


class RiskLimits(BaseModel):
    """Risk limit thresholds for a policy."""

    max_risk_score: Decimal = Decimal("10000")
    max_weight_pct: Decimal = Decimal("5.0")
    custom: dict[str, Decimal] = Field(default_factory=dict)


class AutoApproveConditions(BaseModel):
    """Conditions under which proposals can be auto-approved."""

    max_risk_tier: str = "LOW"
    dry_run_only: bool = True
    max_weight: Decimal = Decimal("2.5")
    min_score: Decimal = Decimal("0.7")


class ActionTiers(BaseModel):
    """Action classification tiers."""

    blocked: list[str] = Field(default_factory=list)
    always_approve: list[str] = Field(default_factory=list)
    auto_approve: list[str] = Field(default_factory=list)
    unrestricted: list[str] = Field(default_factory=list)


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
