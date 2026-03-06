"""Policy-related DTOs."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from .enums import ExecutionMode


class RiskLimits(BaseModel):
    """Risk limit thresholds for a policy."""

    max_risk_score: Decimal = Decimal("10000")
    max_single_allocation_pct: Decimal = Decimal("5.0")
    max_duration: Decimal = Decimal("10.0")
    max_concentration_pct: Decimal = Decimal("25.0")


class AutoApproveConditions(BaseModel):
    """Conditions under which proposals can be auto-approved."""

    max_risk_tier: str = "LOW"
    dry_run_only: bool = True
    max_allocation_pct: Decimal = Decimal("2.5")
    min_confidence: Decimal = Decimal("0.7")


class ActionTiers(BaseModel):
    """Action classification tiers."""

    blocked: list[str] = Field(default_factory=list)
    always_approve: list[str] = Field(
        default_factory=lambda: [
            "action_proposal_medium_risk",
            "action_proposal_high_risk",
            "workflow_transition",
            "config_update",
        ]
    )
    auto_approve: list[str] = Field(
        default_factory=lambda: [
            "action_proposal_low_risk",
            "signal_calculation",
            "data_fetch",
        ]
    )
    unrestricted: list[str] = Field(
        default_factory=lambda: [
            "read_query",
            "status_check",
            "health_monitoring",
        ]
    )


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
