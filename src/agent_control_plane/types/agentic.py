"""Agentic governance DTOs for checkpointing, planning, guardrails, and scorecards."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from .enums import EvaluationDecision, GoalStatus, GuardrailPhase, PlanStepStatus


class SessionCheckpoint(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    event_seq: int
    label: str
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    created_by: str = "system"


class RollbackResult(BaseModel):
    session_id: UUID
    from_seq: int
    to_seq: int
    restored_fields: list[str] = Field(default_factory=list)
    events_appended: int = 0
    warnings: list[str] = Field(default_factory=list)


class Goal(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    name: str
    description: str = ""
    status: GoalStatus = GoalStatus.CREATED
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PlanStep(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    plan_id: UUID
    step_index: int
    title: str
    status: PlanStepStatus = PlanStepStatus.PENDING
    notes: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Plan(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    goal_id: UUID
    title: str
    steps: list[PlanStep] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PlanProgress(BaseModel):
    goal: Goal
    plan: Plan | None = None
    total_steps: int = 0
    completed_steps: int = 0
    failed_steps: int = 0
    running_steps: int = 0


class EvaluationResult(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    operation: str
    decision: EvaluationDecision
    score: float
    reasons: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class GuardrailDecision(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    phase: GuardrailPhase
    allow: bool
    policy_code: str
    reason: str
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class HandoffResult(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    source_agent_id: str
    target_agent_id: str
    allowed_actions: list[str] = Field(default_factory=list)
    accepted: bool = True
    lease_expires_at: datetime | None = None
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ControlPlaneScorecard(BaseModel):
    total_events: int = 0
    checkpoints_created: int = 0
    rollbacks_completed: int = 0
    evaluations_blocked: int = 0
    guardrail_denies: int = 0
    guardrail_allows: int = 0
    handoffs_accepted: int = 0
    handoffs_rejected: int = 0
    budget_denied_count: int = 0
    budget_exhausted_count: int = 0
    evaluation_block_reasons: dict[str, int] = Field(default_factory=dict)
    guardrail_policy_code_counts: dict[str, int] = Field(default_factory=dict)
    approval_latency_ms_p50: float | None = None
    approval_latency_ms_p95: float | None = None
    checkpoint_rollback_latency_ms_p50: float | None = None
    checkpoint_rollback_latency_ms_p95: float | None = None
    avg_cost_per_successful_action: float | None = None
    handoff_accept_rate: float | None = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
