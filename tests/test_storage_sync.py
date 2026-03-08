"""Integration tests for sync SQLAlchemy storage backend (SQLite in-memory)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from agent_control_plane.engine.budget_tracker import BudgetExhaustedError
from agent_control_plane.models.reference import Base, register_models
from agent_control_plane.models.registry import ModelRegistry
from agent_control_plane.storage.sqlalchemy_sync import (
    SyncSqlAlchemyApprovalRepo,
    SyncSqlAlchemyEventRepo,
    SyncSqlAlchemyProposalRepo,
    SyncSqlAlchemySessionRepo,
    SyncSqlAlchemyUnitOfWork,
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
def db_session():
    engine = create_engine("sqlite://", echo=False)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


def test_session_crud(db_session: Session):
    repo = SyncSqlAlchemySessionRepo(db_session)
    cs = repo.create_session(
        session_name="test-1",
        status=SessionStatus.CREATED,
        execution_mode="dry_run",
        max_cost=Decimal("100"),
        max_action_count=10,
    )
    assert cs.session_name == "test-1"

    fetched = repo.get_session(cs.id)
    assert fetched is not None
    assert fetched.id == cs.id

    repo.update_session(cs.id, status=SessionStatus.ACTIVE)
    updated = repo.get_session(cs.id)
    assert updated.status == SessionStatus.ACTIVE

    sessions = repo.list_sessions(statuses=[SessionStatus.ACTIVE])
    assert len(sessions) == 1


def test_budget_operations(db_session: Session):
    repo = SyncSqlAlchemySessionRepo(db_session)
    cs = repo.create_session(
        session_name="budget-test",
        status=SessionStatus.ACTIVE,
        execution_mode="dry_run",
        max_cost=Decimal("100"),
        max_action_count=5,
    )

    info = repo.get_budget(cs.id)
    assert info.remaining_cost == Decimal("100")

    repo.increment_budget(cs.id, Decimal("30"), 2)
    info = repo.get_budget(cs.id)
    assert info.used_cost == Decimal("30")

    with pytest.raises(BudgetExhaustedError):
        repo.increment_budget(cs.id, Decimal("80"), 1)


def test_event_append_and_replay(db_session: Session):
    session_repo = SyncSqlAlchemySessionRepo(db_session)
    event_repo = SyncSqlAlchemyEventRepo(db_session)

    cs = session_repo.create_session(
        session_name="event-test",
        status=SessionStatus.ACTIVE,
        execution_mode="dry_run",
        max_cost=Decimal("100"),
        max_action_count=10,
    )
    session_repo.create_seq_counter(cs.id)

    seq1 = event_repo.append(cs.id, "cycle_started", {"test": True}, state_bearing=True)
    assert seq1 == 1

    seq2 = event_repo.append(cs.id, "cycle_completed", {})
    assert seq2 == 2

    events = event_repo.replay(cs.id)
    assert len(events) == 2
    assert events[0].state_bearing is True
    assert events[1].state_bearing is False

    last = event_repo.get_last_event(cs.id)
    assert last is not None
    assert last.seq == 2
    assert last.state_bearing is False


def test_approval_lifecycle(db_session: Session):
    session_repo = SyncSqlAlchemySessionRepo(db_session)
    approval_repo = SyncSqlAlchemyApprovalRepo(db_session)

    cs = session_repo.create_session(
        session_name="approval-test",
        status=SessionStatus.ACTIVE,
        execution_mode="dry_run",
        max_cost=Decimal("100"),
        max_action_count=10,
    )

    proposal_id = uuid4()
    timeout_at = datetime.now(UTC) + timedelta(hours=1)
    ticket = approval_repo.create_ticket(cs.id, proposal_id, timeout_at)
    assert ticket.status == ApprovalStatus.PENDING

    pending = approval_repo.get_pending_tickets(session_id=cs.id)
    assert len(pending) == 1

    approval_repo.update_ticket(
        ticket.id,
        status=ApprovalStatus.APPROVED,
        decision_type=ApprovalDecisionType.ALLOW_FOR_SESSION,
        scope_resource_ids=["res-1"],
        scope_max_count=3,
    )

    scope_tickets = approval_repo.get_session_scope_tickets(cs.id)
    assert len(scope_tickets) == 1
    assert scope_tickets[0].scope_max_count == 3


def test_deny_all_pending(db_session: Session):
    session_repo = SyncSqlAlchemySessionRepo(db_session)
    approval_repo = SyncSqlAlchemyApprovalRepo(db_session)

    cs = session_repo.create_session(
        session_name="deny-test",
        status=SessionStatus.ACTIVE,
        execution_mode="dry_run",
        max_cost=Decimal("100"),
        max_action_count=10,
    )

    timeout_at = datetime.now(UTC) + timedelta(hours=1)
    approval_repo.create_ticket(cs.id, uuid4(), timeout_at)
    approval_repo.create_ticket(cs.id, uuid4(), timeout_at)

    denied = approval_repo.deny_all_pending(cs.id)
    assert denied == 2


def test_proposal_repo_create_and_query(db_session: Session):
    session_repo = SyncSqlAlchemySessionRepo(db_session)
    proposal_repo = SyncSqlAlchemyProposalRepo(db_session)

    cs = session_repo.create_session(
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
        reasoning="storage sync create proposal",
        metadata={},
        weight=Decimal("1"),
        score=Decimal("0.5"),
        action_tier=ActionTier.ALWAYS_APPROVE,
        risk_level=RiskLevel.LOW,
        status=ProposalStatus.PENDING,
    )
    created = proposal_repo.create_proposal(proposal)
    assert created.id == proposal.id

    fetched = proposal_repo.get_proposal(created.id)
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.resource_id == "res-1"
    assert proposal_repo.has_pending_for_resource(cs.id, "res-1") is True

    proposal_repo.update_status(created.id, ProposalStatus.APPROVED)
    assert proposal_repo.has_pending_for_resource(cs.id, "res-1") is False


def test_unit_of_work(db_session: Session):
    uow = SyncSqlAlchemyUnitOfWork(db_session)
    cs = uow.session_repo.create_session(
        session_name="uow-test",
        status=SessionStatus.ACTIVE,
        execution_mode="dry_run",
        max_cost=Decimal("100"),
        max_action_count=10,
    )
    uow.session_repo.create_seq_counter(cs.id)
    seq = uow.event_repo.append(cs.id, "test_event", {"uow": True})
    assert seq == 1
