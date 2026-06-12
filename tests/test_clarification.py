from decimal import Decimal
from uuid import uuid4

import pytest

from agent_control_plane.engine.event_store import EventStore
from agent_control_plane.engine.policy_engine import PolicyEngine
from agent_control_plane.engine.router import ProposalRouter
from agent_control_plane.engine.session_manager import SessionManager
from agent_control_plane.types.enums import ActionTier, EventKind, ExecutionMode, RiskLevel, SessionStatus
from agent_control_plane.types.policies import PolicySnapshot
from agent_control_plane.types.proposals import ActionProposal
from agent_control_plane.types.sessions import SessionState
from tests.fakes import InMemoryEventRepository


class FakeSessionRepository:
    def __init__(self, session=None):
        self.session = session
        self.updated_fields = {}

    async def get_session(self, session_id):
        return self.session

    async def get_session_for_update(self, session_id):
        return self.session

    async def update_session(self, session_id, **fields):
        self.updated_fields.update(fields)
        if self.session:
            for k, v in fields.items():
                setattr(self.session, k, v)


@pytest.mark.asyncio
async def test_router_clarify_missing_fields():
    policy = PolicySnapshot(
        action_tiers={"steer": ["action1"]}, auto_approve_conditions={"max_risk_tier": RiskLevel.LOW}
    )
    router = ProposalRouter(PolicyEngine(policy))

    proposal = ActionProposal(
        session_id=uuid4(),
        resource_id="res",
        resource_type="task",
        decision="action1",
        reasoning="needs clarification",
        metadata={"missing_fields": ["phone_number"]},
    )

    decision = await router.route(proposal)
    assert decision.tier == ActionTier.CLARIFY
    assert "phone_number" in decision.reason


@pytest.mark.asyncio
async def test_router_clarify_ambiguous_fields():
    policy = PolicySnapshot(
        action_tiers={"steer": ["action1"]}, auto_approve_conditions={"max_risk_tier": RiskLevel.LOW}
    )
    router = ProposalRouter(PolicyEngine(policy))

    proposal = ActionProposal(
        session_id=uuid4(),
        resource_id="res",
        resource_type="task",
        decision="action1",
        reasoning="needs clarification",
        metadata={"address": "Ambiguous"},
    )

    decision = await router.route(proposal)
    assert decision.tier == ActionTier.CLARIFY
    assert "address" in decision.reason


@pytest.mark.asyncio
async def test_router_clarify_suspends_session_and_records_event():
    policy = PolicySnapshot(
        action_tiers={"steer": ["action1"]}, auto_approve_conditions={"max_risk_tier": RiskLevel.LOW}
    )
    repo = InMemoryEventRepository()
    event_store = EventStore(repo)

    session = SessionState(
        id=uuid4(),
        session_name="test-session",
        status=SessionStatus.ACTIVE,
        execution_mode=ExecutionMode.DRY_RUN,
        max_cost=Decimal("100"),
        max_action_count=10,
    )
    session_repo = FakeSessionRepository(session)

    router = ProposalRouter(PolicyEngine(policy), event_store=event_store, session_repository=session_repo)

    proposal = ActionProposal(
        session_id=session.id,
        resource_id="res",
        resource_type="task",
        decision="action1",
        reasoning="needs clarification",
        metadata={"missing_fields": ["email"]},
    )

    decision = await router.route(proposal, session_state=session)
    assert decision.tier == ActionTier.CLARIFY
    assert session.status == SessionStatus.SUSPENDED_FOR_CLARIFICATION
    assert session_repo.updated_fields.get("status") == SessionStatus.SUSPENDED_FOR_CLARIFICATION

    events = await event_store.replay(session.id)
    assert len(events) == 1
    assert events[0].kind == EventKind.CLARIFICATION_REQUESTED
    assert events[0].payload["required_fields"] == ["email"]


@pytest.mark.asyncio
async def test_session_manager_resume_suspended_for_clarification():
    repo = InMemoryEventRepository()
    event_store = EventStore(repo)

    session = SessionState(
        id=uuid4(),
        session_name="test-session",
        status=SessionStatus.SUSPENDED_FOR_CLARIFICATION,
        execution_mode=ExecutionMode.DRY_RUN,
        max_cost=Decimal("100"),
        max_action_count=10,
    )
    session_repo = FakeSessionRepository(session)

    manager = SessionManager(session_repo, event_store=event_store)

    with pytest.raises(ValueError, match="Resolved parameters are required"):
        await manager.resume_session(session.id)

    resumed = await manager.resume_session(session.id, resolved_parameters={"email": "user@example.com"})
    assert resumed.status == SessionStatus.ACTIVE
    assert session.status == SessionStatus.ACTIVE
    assert session_repo.updated_fields.get("status") == SessionStatus.ACTIVE

    events = await event_store.replay(session.id)
    assert len(events) == 1
    assert events[0].kind == EventKind.CLARIFICATION_RESOLVED
    assert events[0].payload["resolved_parameters"] == {"email": "user@example.com"}
