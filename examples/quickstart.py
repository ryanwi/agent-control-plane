"""Minimal runnable quickstart for agent-control-plane.

Run with an async driver available in your environment:

    uv run python examples/quickstart.py

Note: for the default SQLite URL below, install `aiosqlite` first:
`uv pip install aiosqlite`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import DECIMAL, JSON, ForeignKey, String, Text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql.sqltypes import Uuid

from agent_control_plane import (
    ActionTier,
    ApprovalGate,
    BudgetTracker,
    ConcurrencyGuard,
    EventStore,
    PolicyEngine,
    ProposalRouter,
    SessionManager,
)
from agent_control_plane.models.registry import ModelRegistry
from agent_control_plane.types.enums import ApprovalDecisionType, ProposalStatus
from agent_control_plane.types.policies import PolicySnapshotDTO
from agent_control_plane.types.proposals import ActionProposalDTO


class Base(DeclarativeBase):
    """Base class for example-only SQLAlchemy models."""


class ControlSession(Base):
    """Portable subset of ControlSessionMixin fields."""

    __tablename__ = "control_sessions"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    session_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="created", server_default="created")
    execution_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    asset_scope: Mapped[str | None] = mapped_column(String(50), nullable=True)

    max_cost: Mapped[Decimal] = mapped_column(DECIMAL(15, 2), nullable=False)
    used_cost: Mapped[Decimal] = mapped_column(
        DECIMAL(15, 2),
        nullable=False,
        default=Decimal("0"),
        server_default="0",
    )
    max_action_count: Mapped[int] = mapped_column(nullable=False)
    used_action_count: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")

    active_policy_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    active_cycle_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    dry_run_session_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)

    abort_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    abort_details: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default="CURRENT_TIMESTAMP",
    )
    updated_at: Mapped[datetime | None] = mapped_column(nullable=True)


class SessionSeqCounter(Base):
    """Portable subset of SessionSeqCounterMixin fields."""

    __tablename__ = "session_seq_counters"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("control_sessions.id"), nullable=False
    )
    next_seq: Mapped[int] = mapped_column(nullable=False, default=1)


class ControlEvent(Base):
    """Portable subset of ControlEventMixin fields."""

    __tablename__ = "control_events"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("control_sessions.id"), nullable=False)
    seq: Mapped[int] = mapped_column(nullable=False)
    event_kind: Mapped[str] = mapped_column(String(50), nullable=False)
    agent_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    correlation_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    routing_decision: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    routing_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default="CURRENT_TIMESTAMP",
    )


class PolicySnapshot(Base):
    """Portable subset of PolicySnapshotMixin fields."""

    __tablename__ = "policy_snapshots"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    action_tiers: Mapped[dict] = mapped_column(JSON, nullable=False)
    risk_limits: Mapped[dict] = mapped_column(JSON, nullable=False)
    asset_scope: Mapped[str | None] = mapped_column(String(50), nullable=True)
    execution_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    approval_timeout_seconds: Mapped[int] = mapped_column(nullable=False)
    auto_approve_conditions: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default="CURRENT_TIMESTAMP",
    )


class ActionProposal(Base):
    """Portable subset of ActionProposalMixin fields."""

    __tablename__ = "action_proposals"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("control_sessions.id"), nullable=False)
    cycle_event_seq: Mapped[int | None] = mapped_column(nullable=True)

    resource_id: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    weight: Mapped[Decimal] = mapped_column(DECIMAL(8, 4), nullable=False)
    score: Mapped[Decimal] = mapped_column(DECIMAL(5, 4), nullable=False)

    action_tier: Mapped[str] = mapped_column(String(20), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default="CURRENT_TIMESTAMP",
    )


class ApprovalTicket(Base):
    """Portable subset of ApprovalTicketMixin fields."""

    __tablename__ = "approval_tickets"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("control_sessions.id"), nullable=False)
    proposal_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("action_proposals.id"), nullable=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False)
    decision_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(nullable=True)
    timeout_at: Mapped[datetime | None] = mapped_column(nullable=True)

    scope_resource_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    scope_max_cost: Mapped[Decimal | None] = mapped_column(DECIMAL(15, 2), nullable=True)
    scope_max_count: Mapped[int | None] = mapped_column(nullable=True)
    scope_expiry: Mapped[datetime | None] = mapped_column(nullable=True)


DATABASE_URL = "sqlite+aiosqlite:///./agent_control_plane_example.db"


def _register_models() -> None:
    """Register concrete models for control-plane engines."""
    ModelRegistry.register("PolicySnapshot", PolicySnapshot)
    ModelRegistry.register("ControlSession", ControlSession)
    ModelRegistry.register("SessionSeqCounter", SessionSeqCounter)
    ModelRegistry.register("ControlEvent", ControlEvent)
    ModelRegistry.register("ActionProposal", ActionProposal)
    ModelRegistry.register("ApprovalTicket", ApprovalTicket)


def _seed_models() -> PolicySnapshotDTO:
    """Create a small policy snapshot used by router/policy engine."""
    return PolicySnapshotDTO(
        action_tiers={
            "blocked": ["ban"],
            "always_approve": ["refund"],
            "auto_approve": ["status"],
            "unrestricted": [],
        },
        risk_limits={"max_risk_score": "10000", "max_weight_pct": "5.0", "custom": {}},
        asset_scope=None,
        execution_mode="dry_run",
        approval_timeout_seconds=300,
        auto_approve_conditions={
            "max_risk_tier": "LOW",
            "dry_run_only": True,
            "max_weight": "2.5",
            "min_score": "0.7",
        },
    )


async def run_control_flow(db: AsyncSession) -> None:
    """Run a complete proposal through governance + execution guardrails."""
    _register_models()

    session_manager = SessionManager()
    event_store = EventStore()
    policy_snapshot = _seed_models()
    policy = await session_manager.create_policy(
        db,
        action_tiers=policy_snapshot.action_tiers.model_dump(),
        risk_limits=policy_snapshot.risk_limits.model_dump(),
        asset_scope=policy_snapshot.asset_scope,
        execution_mode=(
            policy_snapshot.execution_mode.value
            if hasattr(policy_snapshot.execution_mode, "value")
            else policy_snapshot.execution_mode
        ),
        approval_timeout_seconds=policy_snapshot.approval_timeout_seconds,
        auto_approve_conditions=policy_snapshot.auto_approve_conditions.model_dump(),
    )

    session = await session_manager.create_session(
        db,
        session_name=f"quickstart-session-{uuid4()}",
        execution_mode=policy_snapshot.execution_mode.value
        if hasattr(policy_snapshot.execution_mode, "value")
        else policy_snapshot.execution_mode,
        max_cost=Decimal("100"),
        max_action_count=10,
        policy_id=policy.id,
    )

    engine = PolicyEngine(policy=policy_snapshot)
    router = ProposalRouter(engine)

    proposal = ActionProposalDTO(
        session_id=session.id,
        resource_id="ticket-123",
        resource_type="support_ticket",
        decision="refund",
        reasoning="Customer request under policy",
        metadata={"customer": "alice"},
        weight=Decimal("1.5"),
        score=Decimal("0.9"),
    )
    route = router.route(proposal)

    if route.tier == ActionTier.BLOCKED:
        print("Blocked by policy:", route.reason)
        return

    route_decision = ActionProposal(
        session_id=proposal.session_id,
        resource_id=proposal.resource_id,
        resource_type=proposal.resource_type,
        decision=proposal.decision,
        reasoning=proposal.reasoning,
        metadata_json=proposal.metadata,
        weight=proposal.weight,
        score=proposal.score,
        action_tier=route.tier.value,
        risk_level=route.risk_level.value,
        status=ProposalStatus.PENDING.value,
    )
    db.add(route_decision)
    await db.flush()

    approval_gate = ApprovalGate(event_store)
    guard = ConcurrencyGuard()
    await guard.check_resource_lock(
        db,
        session_id=session.id,
        resource_id=proposal.resource_id,
    )

    ticket = await approval_gate.create_ticket(db, session.id, route_decision.id)
    await approval_gate.approve(
        db,
        ticket.id,
        decision_type=ApprovalDecisionType.ALLOW_FOR_SESSION,
        decided_by="human-ops",
        scope_resource_ids=[proposal.resource_id],
        scope_max_cost=Decimal("20"),
        scope_max_count=3,
    )

    # Consume session-scoped approval instead of requiring another ticket.
    scoped_ticket = await approval_gate.check_session_scope(
        db,
        session_id=session.id,
        resource_id=proposal.resource_id,
        cost=proposal.weight,
    )
    if scoped_ticket is None:
        print("No matching session scope approval available.")
        return

    budget = BudgetTracker()
    if not await budget.check_budget(db, session_id=session.id, cost=proposal.weight, action_count=1):
        print("Session budget exceeded.")
        return

    await budget.increment(db, session.id, cost=proposal.weight, action_count=1)

    await guard.acquire_cycle(db, session_id=session.id, cycle_id=uuid4())

    try:
        await event_store.append(
            db,
            session_id=session.id,
            event_kind="proposal.executed",
            payload={"resource_id": proposal.resource_id},
            state_bearing=True,
            agent_id="quickstart-agent",
            correlation_id=uuid4(),
            routing_decision={"tier": route.tier.value},
            routing_reason=route.reason,
        )

        # Simulate execution by mutating proposal status.
        route_decision.status = ProposalStatus.EXECUTED.value
        await db.flush()
        print(f"Executed proposal {route_decision.id} under session {session.id}")
    finally:
        await guard.release_cycle(db, session_id=session.id)

    await db.commit()
    print("Buffered telemetry events:", event_store.buffer_size)


async def main() -> None:
    if DATABASE_URL.startswith("sqlite+aiosqlite"):
        try:
            import aiosqlite  # noqa: F401
        except ModuleNotFoundError as exc:
            raise SystemExit(
                "Missing optional dependency 'aiosqlite'. Install with: uv pip install aiosqlite"
            ) from exc

    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)

    async with session_maker() as session:
        await run_control_flow(session)


if __name__ == "__main__":
    asyncio.run(main())
