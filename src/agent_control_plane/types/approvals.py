"""Approval-related DTOs."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from .enums import ApprovalDecisionType, ApprovalStatus
from .ids import ResourceId


class ApprovalScope(BaseModel):
    """Scope constraints for an approval decision."""

    resource_ids: list[ResourceId] = Field(default_factory=list)
    max_cost: Decimal | None = None
    max_count: int | None = None
    expiry: datetime | None = None


class ApprovalTicket(BaseModel):
    """Human-in-the-loop approval ticket."""

    id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    proposal_id: UUID

    # Flat scope fields (match ORM column names)
    scope_resource_ids: list[ResourceId] | None = None
    scope_max_cost: Decimal | None = None
    scope_max_count: int | None = None
    scope_expiry: datetime | None = None

    status: ApprovalStatus = ApprovalStatus.PENDING
    decision_type: ApprovalDecisionType | None = None
    decided_by: str | None = None
    decision_reason: str | None = None
    timeout_at: datetime | None = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    decided_at: datetime | None = None


class ApprovalDecisionRequest(BaseModel):
    """Request to approve or deny a ticket."""

    decision_type: ApprovalDecisionType = ApprovalDecisionType.ALLOW_ONCE
    reason: str | None = None
    decided_by: str = "operator"

    # Optional scope override (for allow_for_session)
    scope: ApprovalScope | None = None
