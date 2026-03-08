"""Agentic governance DTOs for checkpointing, planning, guardrails, and scorecards."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from .enums import EvaluationDecision, GoalStatus, GuardrailPhase, PlanStepStatus


class SessionCheckpointDTO(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    event_seq: int
    label: str
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    created_by: str = "system"


class RollbackResultDTO(BaseModel):
    session_id: UUID
    from_seq: int
    to_seq: int
    restored_fields: list[str] = Field(default_factory=list)
    events_appended: int = 0
    warnings: list[str] = Field(default_factory=list)


class GoalDTO(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    name: str
    description: str = ""
    status: GoalStatus = GoalStatus.CREATED
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PlanStepDTO(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    plan_id: UUID
    step_index: int
    title: str
    status: PlanStepStatus = PlanStepStatus.PENDING
    notes: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PlanDTO(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    goal_id: UUID
    title: str
    steps: list[PlanStepDTO] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PlanProgressDTO(BaseModel):
    goal: GoalDTO
    plan: PlanDTO | None = None
    total_steps: int = 0
    completed_steps: int = 0
    failed_steps: int = 0
    running_steps: int = 0


class EvaluationResultDTO(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    operation: str
    decision: EvaluationDecision
    score: float
    reasons: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class GuardrailDecisionDTO(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    phase: GuardrailPhase
    allow: bool
    policy_code: str
    reason: str
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class HandoffResultDTO(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    source_agent_id: str
    target_agent_id: str
    allowed_actions: list[str] = Field(default_factory=list)
    accepted: bool = True
    lease_expires_at: datetime | None = None
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ControlPlaneScorecardDTO(BaseModel):
    total_events: int = 0
    checkpoints_created: int = 0
    rollbacks_completed: int = 0
    evaluations_blocked: int = 0
    guardrail_denies: int = 0
    handoffs_accepted: int = 0
    handoffs_rejected: int = 0
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
