"""Token governance DTOs for identity-scoped budget enforcement."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from .enums import BudgetPeriod, ModelTier
from .ids import ModelId, OrgId, TeamId, UserId


class IdentityContext(BaseModel):
    """Who is consuming tokens."""

    user_id: UserId | None = None
    org_id: OrgId | None = None
    team_id: TeamId | None = None


class TokenUsage(BaseModel):
    """Token counts for a single interaction."""

    model_id: ModelId
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost_usd: Decimal


class TokenBudgetConfig(BaseModel):
    """Budget limits for an identity within a time period."""

    id: UUID = Field(default_factory=uuid4)
    identity: IdentityContext
    period: BudgetPeriod
    max_tokens: int | None = None
    max_cost_usd: Decimal | None = None
    allowed_models: list[ModelId] | None = None


class TokenBudgetState(BaseModel):
    """Current accumulated usage against a budget config."""

    config_id: UUID
    identity: IdentityContext
    period: BudgetPeriod
    window_start: datetime
    window_end: datetime
    used_tokens: int
    used_cost_usd: Decimal
    remaining_tokens: int | None = None
    remaining_cost_usd: Decimal | None = None


class TokenBudgetCheckResult(BaseModel):
    """Result of budget check."""

    allowed: bool
    denial_reasons: list[str] = Field(default_factory=list)
    budget_states: list[TokenBudgetState] = Field(default_factory=list)


class ModelAccessResult(BaseModel):
    """Result of model access check."""

    allowed: bool
    model_id: ModelId
    model_tier: ModelTier
    denial_reason: str | None = None


class TokenUsageSummary(BaseModel):
    """Aggregated usage for reporting."""

    identity: IdentityContext
    period: BudgetPeriod
    window_start: datetime
    window_end: datetime
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    total_cost_usd: Decimal
    model_breakdown: dict[str, int] = Field(default_factory=dict)
    action_count: int = 0


class ModelGovernancePolicy(BaseModel):
    """Model access rules."""

    model_tier_assignments: dict[str, ModelTier] = Field(default_factory=dict)
    tier_restrictions: dict[str, list[str]] = Field(default_factory=dict)
    identity_overrides: dict[str, list[str]] = Field(default_factory=dict)
