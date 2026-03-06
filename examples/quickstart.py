"""Minimal runnable quickstart for agent-control-plane.

Run with an async driver available in your environment:

    uv run python examples/quickstart.py

Note: for the default SQLite URL below, install `aiosqlite` first:
`uv pip install aiosqlite`.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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
from agent_control_plane.models.reference import (
    ActionProposal,
    Base,
    register_models,
)
from agent_control_plane.types.enums import ApprovalDecisionType, ProposalStatus
from agent_control_plane.types.policies import PolicySnapshotDTO
from agent_control_plane.types.proposals import ActionProposalDTO

DATABASE_URL = "sqlite+aiosqlite:///./agent_control_plane_example.db"


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
    register_models()

    session_manager = SessionManager()
    event_store = EventStore()
    policy_snapshot = _seed_models()
    policy = await session_manager.create_policy(
        db,
        action_tiers=policy_snapshot.action_tiers.model_dump(mode="json"),
        risk_limits=policy_snapshot.risk_limits.model_dump(mode="json"),
        asset_scope=policy_snapshot.asset_scope,
        execution_mode=(
            policy_snapshot.execution_mode.value
            if hasattr(policy_snapshot.execution_mode, "value")
            else policy_snapshot.execution_mode
        ),
        approval_timeout_seconds=policy_snapshot.approval_timeout_seconds,
        auto_approve_conditions=policy_snapshot.auto_approve_conditions.model_dump(mode="json"),
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

    # Check resource lock before persisting a pending proposal row to avoid self-locking in the example flow.
    guard = ConcurrencyGuard()
    await guard.check_resource_lock(
        db,
        session_id=session.id,
        resource_id=proposal.resource_id,
    )

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
            raise SystemExit("Missing optional dependency 'aiosqlite'. Install with: uv pip install aiosqlite") from exc

    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)

    async with session_maker() as session:
        await run_control_flow(session)


if __name__ == "__main__":
    asyncio.run(main())
