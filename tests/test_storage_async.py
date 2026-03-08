"""Integration tests for async SQLAlchemy storage backend (SQLite in-memory)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent_control_plane.engine.budget_tracker import BudgetExhaustedError
from agent_control_plane.models.reference import Base, register_models
from agent_control_plane.models.registry import ModelRegistry
from agent_control_plane.storage.sqlalchemy_async import (
    AsyncSqlAlchemyApprovalRepo,
    AsyncSqlAlchemyEventRepo,
    AsyncSqlAlchemyProposalRepo,
    AsyncSqlAlchemySessionRepo,
    AsyncSqlAlchemyUnitOfWork,
)
from agent_control_plane.types.enums import (
    ActionName,
    ActionTier,
    ApprovalDecisionType,
    ApprovalStatus,
    ProposalStatus,
    RiskLevel,
    SessionStatus,
)
from agent_control_plane.types.proposals import ActionProposalDTO


@pytest.fixture(autouse=True)
def _register():
    register_models()
    yield
    ModelRegistry.reset()


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_session_crud(db_session: AsyncSession):
    repo = AsyncSqlAlchemySessionRepo(db_session)
    cs = await repo.create_session(
        session_name="test-1",
        status=SessionStatus.CREATED,
        execution_mode="dry_run",
        max_cost=Decimal("100"),
        max_action_count=10,
    )
    assert cs.session_name == "test-1"
    assert cs.status == SessionStatus.CREATED

    fetched = await repo.get_session(cs.id)
    assert fetched is not None
    assert fetched.id == cs.id

    await repo.update_session(cs.id, status=SessionStatus.ACTIVE)
    updated = await repo.get_session(cs.id)
    assert updated.status == SessionStatus.ACTIVE

    sessions = await repo.list_sessions(statuses=[SessionStatus.ACTIVE])
    assert len(sessions) == 1


@pytest.mark.asyncio
async def test_budget_operations(db_session: AsyncSession):
    repo = AsyncSqlAlchemySessionRepo(db_session)
    cs = await repo.create_session(
        session_name="budget-test",
        status=SessionStatus.ACTIVE,
        execution_mode="dry_run",
        max_cost=Decimal("100"),
        max_action_count=5,
    )

    info = await repo.get_budget(cs.id)
    assert info.remaining_cost == Decimal("100")
    assert info.remaining_count == 5

    await repo.increment_budget(cs.id, Decimal("30"), 2)
    info = await repo.get_budget(cs.id)
    assert info.used_cost == Decimal("30")
    assert info.used_count == 2

    with pytest.raises(BudgetExhaustedError):
        await repo.increment_budget(cs.id, Decimal("80"), 1)


@pytest.mark.asyncio
async def test_event_append_and_replay(db_session: AsyncSession):
    session_repo = AsyncSqlAlchemySessionRepo(db_session)
    event_repo = AsyncSqlAlchemyEventRepo(db_session)

    cs = await session_repo.create_session(
        session_name="event-test",
        status=SessionStatus.ACTIVE,
        execution_mode="dry_run",
        max_cost=Decimal("100"),
        max_action_count=10,
    )
    await session_repo.create_seq_counter(cs.id)

    seq1 = await event_repo.append(cs.id, "cycle_started", {"test": True}, state_bearing=True)
    assert seq1 == 1

    seq2 = await event_repo.append(cs.id, "cycle_completed", {})
    assert seq2 == 2

    events = await event_repo.replay(cs.id)
    assert len(events) == 2
    assert events[0].seq == 1
    assert events[1].seq == 2
    assert events[0].state_bearing is True
    assert events[1].state_bearing is False

    last = await event_repo.get_last_event(cs.id)
    assert last is not None
    assert last.seq == 2
    assert last.state_bearing is False


@pytest.mark.asyncio
async def test_approval_lifecycle(db_session: AsyncSession):
    session_repo = AsyncSqlAlchemySessionRepo(db_session)
    approval_repo = AsyncSqlAlchemyApprovalRepo(db_session)

    cs = await session_repo.create_session(
        session_name="approval-test",
        status=SessionStatus.ACTIVE,
        execution_mode="dry_run",
        max_cost=Decimal("100"),
        max_action_count=10,
    )

    proposal_id = uuid4()
    timeout_at = datetime.now(UTC) + timedelta(hours=1)
    ticket = await approval_repo.create_ticket(cs.id, proposal_id, timeout_at)
    assert ticket.status == ApprovalStatus.PENDING

    pending = await approval_repo.get_pending_tickets(session_id=cs.id)
    assert len(pending) == 1

    fetched = await approval_repo.get_pending_ticket_for_update(ticket.id)
    assert fetched.id == ticket.id

    await approval_repo.update_ticket(
        ticket.id,
        status=ApprovalStatus.APPROVED,
        decision_type=ApprovalDecisionType.ALLOW_FOR_SESSION,
        scope_resource_ids=["res-1"],
        scope_max_count=3,
    )

    scope_tickets = await approval_repo.get_session_scope_tickets(cs.id)
    assert len(scope_tickets) == 1
    assert scope_tickets[0].scope_max_count == 3


@pytest.mark.asyncio
async def test_deny_all_pending(db_session: AsyncSession):
    session_repo = AsyncSqlAlchemySessionRepo(db_session)
    approval_repo = AsyncSqlAlchemyApprovalRepo(db_session)

    cs = await session_repo.create_session(
        session_name="deny-test",
        status=SessionStatus.ACTIVE,
        execution_mode="dry_run",
        max_cost=Decimal("100"),
        max_action_count=10,
    )

    timeout_at = datetime.now(UTC) + timedelta(hours=1)
    await approval_repo.create_ticket(cs.id, uuid4(), timeout_at)
    await approval_repo.create_ticket(cs.id, uuid4(), timeout_at)

    denied = await approval_repo.deny_all_pending(cs.id)
    assert denied == 2


@pytest.mark.asyncio
async def test_proposal_repo(db_session: AsyncSession):
    session_repo = AsyncSqlAlchemySessionRepo(db_session)
    proposal_repo = AsyncSqlAlchemyProposalRepo(db_session)

    cs = await session_repo.create_session(
        session_name="proposal-test",
        status=SessionStatus.ACTIVE,
        execution_mode="dry_run",
        max_cost=Decimal("100"),
        max_action_count=10,
    )

    proposal = ActionProposalDTO(
        session_id=cs.id,
        resource_id="res-1",
        resource_type="task",
        decision=ActionName.STATUS,
        reasoning="test",
        metadata={},
        weight=Decimal("1"),
        score=Decimal("0.5"),
        action_tier=ActionTier.ALWAYS_APPROVE,
        risk_level=RiskLevel.LOW,
        status=ProposalStatus.PENDING,
    )
    created = await proposal_repo.create_proposal(proposal)
    assert created.id == proposal.id

    assert await proposal_repo.has_pending_for_resource(cs.id, "res-1") is True
    assert await proposal_repo.has_pending_for_resource(cs.id, "res-2") is False

    await proposal_repo.update_status(created.id, ProposalStatus.APPROVED)
    assert await proposal_repo.has_pending_for_resource(cs.id, "res-1") is False


@pytest.mark.asyncio
async def test_unit_of_work(db_session: AsyncSession):
    uow = AsyncSqlAlchemyUnitOfWork(db_session)
    cs = await uow.session_repo.create_session(
        session_name="uow-test",
        status=SessionStatus.ACTIVE,
        execution_mode="dry_run",
        max_cost=Decimal("100"),
        max_action_count=10,
    )
    await uow.session_repo.create_seq_counter(cs.id)
    seq = await uow.event_repo.append(cs.id, "test_event", {"uow": True})
    assert seq == 1
