"""Session-related DTOs."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field

from .enums import AbortReason, AssetScope, ExecutionMode, KillSwitchScope, SessionStatus


class SessionCreate(BaseModel):
    """Request to create a new control session."""

    session_name: str
    execution_mode: ExecutionMode = ExecutionMode.DRY_RUN
    asset_scope: AssetScope | None = None
    max_cost: Decimal = Decimal("100000")
    max_action_count: int = 50
    policy_id: UUID | None = None
    dry_run_session_id: UUID | None = None


class SessionState(BaseModel):
    """Current state of a control session."""

    id: UUID
    session_name: str
    status: SessionStatus
    execution_mode: ExecutionMode
    asset_scope: AssetScope | None = None

    # Budget
    max_cost: Decimal
    used_cost: Decimal = Decimal("0")
    max_action_count: int
    used_action_count: int = 0

    # References
    active_policy_id: UUID | None = None
    active_cycle_id: UUID | None = None
    dry_run_session_id: UUID | None = None

    # Abort info
    abort_reason: AbortReason | None = None
    abort_details: str | None = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = None


class BudgetInfo(BaseModel):
    """Budget status for a control session."""

    remaining_cost: Decimal
    remaining_count: int
    used_cost: Decimal
    used_count: int
    max_cost: Decimal
    max_count: int


class SessionSummary(BaseModel):
    """Lightweight session summary for list views."""

    id: UUID
    session_name: str
    status: SessionStatus
    execution_mode: ExecutionMode
    used_cost: Decimal = Decimal("0")
    max_cost: Decimal
    used_action_count: int = 0
    max_action_count: int
    created_at: datetime


class KillSwitchResult(BaseModel):
    """Typed kill-switch operation result."""

    scope: KillSwitchScope
    session_id: UUID | None = None
    agent_id: str | None = None
    sessions_aborted: int | None = None
    sessions_affected: int | None = None
    tickets_denied: int = 0
