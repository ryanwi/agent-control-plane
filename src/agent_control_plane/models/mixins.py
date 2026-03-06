"""SQLAlchemy mixin classes for control plane models.

Each mixin provides column definitions without inheriting from Base.
Host applications compose these with their own Base class:

    class PolicySnapshot(Base, PolicySnapshotMixin):
        __tablename__ = "policy_snapshots"
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DECIMAL, JSON, TIMESTAMP, VARCHAR, Text, func
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column


class PolicySnapshotMixin:
    """Mixin for policy snapshot model."""

    action_tiers: Mapped[dict] = mapped_column(JSON, nullable=False)
    risk_limits: Mapped[dict] = mapped_column(JSON, nullable=False)
    asset_scope: Mapped[str | None] = mapped_column(VARCHAR(50), nullable=True)
    execution_mode: Mapped[str] = mapped_column(VARCHAR(20), nullable=False, default="dry_run")
    approval_timeout_seconds: Mapped[int] = mapped_column(nullable=False, default=3600)
    auto_approve_conditions: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        default=func.current_timestamp(),
        server_default=func.current_timestamp(),
    )


class ControlSessionMixin:
    """Mixin for control session model."""

    session_name: Mapped[str] = mapped_column(VARCHAR(255), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(VARCHAR(20), nullable=False, default="created", server_default="created")
    execution_mode: Mapped[str] = mapped_column(VARCHAR(20), nullable=False, default="dry_run")
    asset_scope: Mapped[str | None] = mapped_column(VARCHAR(50), nullable=True)

    # Budget tracking
    max_cost: Mapped[Decimal] = mapped_column(DECIMAL(15, 2), nullable=False, default=Decimal("100000"))
    used_cost: Mapped[Decimal] = mapped_column(DECIMAL(15, 2), nullable=False, default=Decimal("0"), server_default="0")
    max_action_count: Mapped[int] = mapped_column(nullable=False, default=50)
    used_action_count: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")

    # Cycle tracking
    active_cycle_id: Mapped[UUID | None] = mapped_column(PostgresUUID(as_uuid=True), nullable=True)

    # Abort info
    abort_reason: Mapped[str | None] = mapped_column(VARCHAR(50), nullable=True)
    abort_details: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        default=func.current_timestamp(),
        server_default=func.current_timestamp(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True, onupdate=func.current_timestamp()
    )


class SessionSeqCounterMixin:
    """Mixin for session sequence counter model."""

    next_seq: Mapped[int] = mapped_column(nullable=False, default=1, server_default="1")


class ControlEventMixin:
    """Mixin for control event model."""

    seq: Mapped[int] = mapped_column(nullable=False)
    event_kind: Mapped[str] = mapped_column(VARCHAR(50), nullable=False)
    agent_id: Mapped[str | None] = mapped_column(VARCHAR(100), nullable=True)
    correlation_id: Mapped[UUID | None] = mapped_column(PostgresUUID(as_uuid=True), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    routing_decision: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    routing_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        default=func.current_timestamp(),
        server_default=func.current_timestamp(),
    )


class ActionProposalMixin:
    """Mixin for action proposal model."""

    cycle_event_seq: Mapped[int | None] = mapped_column(nullable=True)

    # Proposal content
    resource_id: Mapped[str] = mapped_column(VARCHAR(50), nullable=False)
    resource_type: Mapped[str] = mapped_column(VARCHAR(50), nullable=False)
    decision: Mapped[str] = mapped_column(VARCHAR(20), nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # Scoring (domain-specific)
    weight: Mapped[Decimal] = mapped_column(DECIMAL(8, 4), nullable=False, default=Decimal("0"))
    score: Mapped[Decimal] = mapped_column(DECIMAL(5, 4), nullable=False, default=Decimal("0"))

    # Classification
    action_tier: Mapped[str] = mapped_column(VARCHAR(20), nullable=False, default="always_approve")
    risk_level: Mapped[str] = mapped_column(VARCHAR(10), nullable=False, default="medium")
    status: Mapped[str] = mapped_column(VARCHAR(20), nullable=False, default="pending", server_default="pending")

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        default=func.current_timestamp(),
        server_default=func.current_timestamp(),
    )


class RiskDecisionMixin:
    """Mixin for risk decision model."""

    # Risk metrics
    risk_score: Mapped[Decimal] = mapped_column(DECIMAL(15, 2), nullable=False)
    risk_details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    passed: Mapped[bool] = mapped_column(nullable=False, default=True)

    assessed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        default=func.current_timestamp(),
        server_default=func.current_timestamp(),
    )


class ApprovalTicketMixin:
    """Mixin for approval ticket model."""

    # Scope constraints
    scope_max_cost: Mapped[Decimal | None] = mapped_column(DECIMAL(15, 2), nullable=True)
    scope_max_count: Mapped[int | None] = mapped_column(nullable=True)
    scope_expiry: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    # Decision
    status: Mapped[str] = mapped_column(VARCHAR(20), nullable=False, default="pending", server_default="pending")
    decision_type: Mapped[str | None] = mapped_column(VARCHAR(30), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(VARCHAR(100), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    timeout_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        default=func.current_timestamp(),
        server_default=func.current_timestamp(),
    )
    decided_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


class ExecutionIntentMixin:
    """Mixin for execution intent model."""

    executor_type: Mapped[str] = mapped_column(VARCHAR(20), nullable=False, default="dry_run")

    resource_id: Mapped[str] = mapped_column(VARCHAR(50), nullable=False)
    action: Mapped[str] = mapped_column(VARCHAR(20), nullable=False)
    parameters_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    status: Mapped[str] = mapped_column(VARCHAR(20), nullable=False, default="pending", server_default="pending")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        default=func.current_timestamp(),
        server_default=func.current_timestamp(),
    )


class ExecutionResultMixin:
    """Mixin for execution result model."""

    execution_id: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True)

    success: Mapped[bool] = mapped_column(nullable=False, default=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_duration_ms: Mapped[int | None] = mapped_column(nullable=True)
    result_details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    completed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        default=func.current_timestamp(),
        server_default=func.current_timestamp(),
    )


class ReleaseCandidateMixin:
    """Mixin for release candidate model."""

    dry_run_metrics: Mapped[dict] = mapped_column(JSON, nullable=False)
    promotion_status: Mapped[str] = mapped_column(
        VARCHAR(20), nullable=False, default="pending", server_default="pending"
    )
    promoted_by: Mapped[str | None] = mapped_column(VARCHAR(100), nullable=True)
    promoted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        default=func.current_timestamp(),
        server_default=func.current_timestamp(),
    )
