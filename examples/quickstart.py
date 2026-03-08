"""Minimal runnable async quickstart for agent-control-plane.

Run:
    uv run python examples/quickstart.py

Note: for the default SQLite URL below, install `aiosqlite` first:
`uv pip install aiosqlite`.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_control_plane.engine.approval_gate import ApprovalGate
from agent_control_plane.engine.budget_tracker import BudgetTracker
from agent_control_plane.engine.concurrency import ConcurrencyGuard
from agent_control_plane.engine.event_store import EventStore
from agent_control_plane.engine.policy_engine import PolicyEngine
from agent_control_plane.engine.router import ProposalRouter
from agent_control_plane.engine.session_manager import SessionManager
from agent_control_plane.models.reference import ActionProposal, Base, register_models
from agent_control_plane.storage.sqlalchemy_async import AsyncSqlAlchemyUnitOfWork
from agent_control_plane.types.enums import (
    ActionName,
    ActionTier,
    ApprovalDecisionType,
    EventKind,
    ExecutionMode,
    ProposalStatus,
    RiskLevel,
)
from agent_control_plane.types.policies import PolicySnapshot

DATABASE_URL = "sqlite+aiosqlite:///./agent_control_plane_example.db"


def _seed_policy() -> PolicySnapshot:
    return PolicySnapshot(
        action_tiers={
            "blocked": [ActionName.BAN],
            "always_approve": [ActionName.REFUND],
            "auto_approve": [ActionName.STATUS],
            "unrestricted": [],
        },
        risk_limits={"max_risk_score": "10000", "max_weight_pct": "5.0", "custom": {}},
        execution_mode=ExecutionMode.DRY_RUN,
        approval_timeout_seconds=300,
        auto_approve_conditions={
            "max_risk_tier": RiskLevel.LOW,
            "dry_run_only": True,
            "max_weight": "2.5",
            "min_score": "0.7",
        },
    )


async def run_control_flow(uow: AsyncSqlAlchemyUnitOfWork) -> None:
    policy_snapshot = _seed_policy()
    session_manager = SessionManager(uow.session_repo)
    event_store = EventStore(uow.event_repo)
    approval_gate = ApprovalGate(event_store, uow.approval_repo, uow.proposal_repo)
    budget = BudgetTracker(uow.session_repo)
    guard = ConcurrencyGuard(uow.session_repo, uow.proposal_repo)
    router = ProposalRouter(PolicyEngine(policy_snapshot))

    policy_id = await session_manager.create_policy(
        action_tiers=policy_snapshot.action_tiers.model_dump(mode="json"),
        risk_limits=policy_snapshot.risk_limits.model_dump(mode="json"),
        asset_scope=policy_snapshot.asset_scope,
        execution_mode=policy_snapshot.execution_mode.value,
        approval_timeout_seconds=policy_snapshot.approval_timeout_seconds,
        auto_approve_conditions=policy_snapshot.auto_approve_conditions.model_dump(mode="json"),
    )
    session = await session_manager.create_session(
        session_name=f"quickstart-session-{uuid4()}",
        execution_mode=policy_snapshot.execution_mode.value,
        max_cost=Decimal("100"),
        max_action_count=10,
        policy_id=policy_id,
    )

    proposal = ActionProposal(
        session_id=session.id,
        resource_id="ticket-123",
        resource_type="support_ticket",
        decision=ActionName.REFUND,
        reasoning="Customer request under policy",
        metadata={"customer": "alice"},
        weight=Decimal("1.5"),
        score=Decimal("0.9"),
    )
    route = router.route(proposal)
    if route.tier == ActionTier.BLOCKED:
        print("Blocked by policy:", route.reason)
        return

    await guard.check_resource_lock(session.id, proposal.resource_id)

    action = ActionProposal(
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
    uow._session.add(action)
    await uow._session.flush()

    ticket = await approval_gate.create_ticket(session.id, action.id)
    await approval_gate.approve(
        ticket.id,
        decision_type=ApprovalDecisionType.ALLOW_FOR_SESSION,
        decided_by="human-ops",
        scope_resource_ids=[proposal.resource_id],
        scope_max_cost=Decimal("20"),
        scope_max_count=3,
    )

    scoped = await approval_gate.check_session_scope(
        session_id=session.id,
        resource_id=proposal.resource_id,
        cost=proposal.weight,
    )
    if scoped is None:
        print("No matching session scope approval available.")
        return

    if not await budget.check_budget(session.id, cost=proposal.weight, action_count=1):
        print("Session budget exceeded.")
        return
    await budget.increment(session.id, cost=proposal.weight, action_count=1)

    await guard.acquire_cycle(session.id, cycle_id=uuid4())
    try:
        await event_store.append(
            session_id=session.id,
            event_kind=EventKind.EXECUTION_COMPLETED,
            payload={"resource_id": proposal.resource_id},
            state_bearing=True,
            agent_id="quickstart-agent",
            correlation_id=uuid4(),
            routing_decision={"tier": route.tier.value},
            routing_reason=route.reason,
        )
        action.status = ProposalStatus.EXECUTED.value
        await uow._session.flush()
        print(f"Executed proposal {action.id} under session {session.id}")
    finally:
        await guard.release_cycle(session.id)

    await uow.commit()
    print("Buffered telemetry events:", event_store.buffer_size)


async def main() -> None:
    if DATABASE_URL.startswith("sqlite+aiosqlite"):
        try:
            import aiosqlite  # noqa: F401
        except ModuleNotFoundError as exc:
            raise SystemExit("Missing optional dependency 'aiosqlite'. Install with: uv pip install aiosqlite") from exc

    db_path = Path("./agent_control_plane_example.db")
    db_path.unlink(missing_ok=True)

    register_models()
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_maker() as session:
        await run_control_flow(AsyncSqlAlchemyUnitOfWork(session))
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
