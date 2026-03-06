"""Action proposal and execution domain DTOs."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

from .enums import (
    ActionName,
    ActionTier,
    ExecutionIntentStatus,
    ProposalStatus,
    RiskLevel,
    parse_action_name,
)


class ActionProposalDTO(BaseModel):
    """An action proposal generated from an agent recommendation."""

    id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    cycle_event_seq: int | None = None

    # Proposal content
    resource_id: str
    resource_type: str
    decision: ActionName
    reasoning: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    # Optional scoring (domain-specific classifiers can use these)
    weight: Decimal = Decimal("0")
    score: Decimal = Decimal("0")
    risk_factors: list[str] = Field(default_factory=list)
    supporting_signals: list[str] = Field(default_factory=list)

    # Classification
    action_tier: ActionTier = ActionTier.ALWAYS_APPROVE
    risk_level: RiskLevel = RiskLevel.MEDIUM
    status: ProposalStatus = ProposalStatus.PENDING

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("decision", mode="before")
    @classmethod
    def _parse_decision(cls, value: ActionName | str) -> ActionName:
        return parse_action_name(value)


class RiskDecisionDTO(BaseModel):
    """Risk assessment result for an action proposal."""

    id: UUID = Field(default_factory=uuid4)
    proposal_id: UUID

    # Risk metrics
    risk_score: Decimal
    risk_details: dict[str, Any] = Field(default_factory=dict)

    risk_warnings: list[str] = Field(default_factory=list)
    passed: bool = True
    rejection_reasons: list[str] = Field(default_factory=list)

    assessed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ExecutionIntentDTO(BaseModel):
    """Intent to execute an approved proposal."""

    id: UUID = Field(default_factory=uuid4)
    proposal_id: UUID
    executor_type: str  # dry_run, live, replay

    resource_id: str
    action: str
    parameters: dict[str, Any] = Field(default_factory=dict)

    status: ExecutionIntentStatus = ExecutionIntentStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ExecutionResultDTO(BaseModel):
    """Outcome of executing an action."""

    id: UUID = Field(default_factory=uuid4)
    intent_id: UUID
    execution_id: str | None = None

    success: bool = False
    error_message: str | None = None
    execution_duration_ms: int | None = None
    result_details: dict[str, Any] = Field(default_factory=dict)

    completed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
