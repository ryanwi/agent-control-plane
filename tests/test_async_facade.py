"""Tests for AsyncControlPlaneFacade."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from agent_control_plane.async_facade import AsyncControlPlaneFacade
from agent_control_plane.engine.concurrency import CycleAlreadyActiveError
from agent_control_plane.models.registry import ModelRegistry
from agent_control_plane.sync import DictEventMapper
from agent_control_plane.types.enums import (
    ActionName,
    ActionTier,
    ApprovalDecisionType,
    ApprovalStatus,
    EventKind,
    ProposalStatus,
    RiskLevel,
    SessionStatus,
    UnknownAppEventPolicy,
)


@pytest.mark.asyncio
async def test_async_facade_session_budget_emit_and_close(tmp_path: Path):
    db_file = tmp_path / "cp_async_facade.db"
    facade = AsyncControlPlaneFacade.from_database_url(
        f"sqlite+aiosqlite:///{db_file}",
        mapper=DictEventMapper({"started": EventKind.CYCLE_STARTED}),
        unknown_policy=UnknownAppEventPolicy.RAISE,
    )

    sid = await facade.open_session("async-demo", max_cost=Decimal("20"), max_action_count=2)
    assert await facade.check_budget(sid, cost=Decimal("5"), action_count=1) is True
    await facade.increment_budget(sid, cost=Decimal("5"), action_count=1)

    seq = await facade.emit_app(sid, "started", {"k": "v"}, state_bearing=True, agent_id="agent-a")
    assert seq == 1

    close_result = await facade.close_session(sid)
    assert close_result.session.status == SessionStatus.COMPLETED
    assert close_result.events_appended == 0

    events = await facade.replay(sid)
    assert len(events) == 1
    assert events[0].state_bearing is True

    await facade.close()


@pytest.mark.asyncio
async def test_async_facade_session_transitions_and_cycle_lock(tmp_path: Path):
    db_file = tmp_path / "cp_async_transitions.db"
    facade = AsyncControlPlaneFacade.from_database_url(f"sqlite+aiosqlite:///{db_file}")

    sid = await facade.open_session("transitions")
    listed = await facade.list_sessions(statuses=[SessionStatus.CREATED])
    assert any(s.id == sid for s in listed)

    activated = await facade.activate_session(sid)
    assert activated.session.status == SessionStatus.ACTIVE

    cycle_id = uuid4()
    await facade.acquire_cycle(sid, cycle_id)
    with pytest.raises(CycleAlreadyActiveError):
        await facade.acquire_cycle(sid, uuid4())
    await facade.release_cycle(sid)

    paused = await facade.pause_session(sid)
    assert paused.session.status == SessionStatus.PAUSED

    resumed = await facade.resume_session(sid)
    assert resumed.session.status == SessionStatus.ACTIVE

    await facade.close()


@pytest.mark.asyncio
async def test_async_facade_approval_lifecycle_and_expiry(tmp_path: Path):
    db_file = tmp_path / "cp_async_approvals.db"
    facade = AsyncControlPlaneFacade.from_database_url(f"sqlite+aiosqlite:///{db_file}")

    sid = await facade.open_session("approvals")
    await facade.activate_session(sid)

    async with facade.session_scope() as db:
        proposal_model = ModelRegistry.get("ActionProposal")
        proposal = proposal_model(
            id=uuid4(),
            session_id=sid,
            cycle_event_seq=None,
            resource_id="resource-1",
            resource_type="task",
            decision=ActionName.STATUS,
            reasoning="needs approval",
            metadata_json={},
            weight=Decimal("1.0"),
            score=Decimal("0.8"),
            action_tier=ActionTier.ALWAYS_APPROVE,
            risk_level=RiskLevel.MEDIUM,
            status=ProposalStatus.PENDING,
        )
        db.add(proposal)
        await db.commit()
        proposal_id = proposal.id

    ticket = await facade.create_ticket(sid, proposal_id, datetime.now(UTC) + timedelta(minutes=5))
    approved = await facade.approve_ticket(
        ticket.id,
        decision_type=ApprovalDecisionType.ALLOW_FOR_SESSION,
        scope_resource_ids=["resource-1"],
        scope_max_cost=Decimal("100"),
        scope_max_action_count=2,
    )
    assert approved.status == ApprovalStatus.APPROVED
    assert approved.decision_type == ApprovalDecisionType.ALLOW_FOR_SESSION

    async with facade.session_scope() as db:
        proposal_model = ModelRegistry.get("ActionProposal")
        row = await db.get(proposal_model, proposal_id)
        assert row.status == ProposalStatus.APPROVED

    ticket2 = await facade.create_ticket(sid, proposal_id, datetime.now(UTC) + timedelta(minutes=5))
    denied = await facade.deny_ticket(ticket2.id, reason="manual deny")
    assert denied.status == ApprovalStatus.DENIED

    ticket3 = await facade.create_ticket(sid, proposal_id, datetime.now(UTC) - timedelta(minutes=1))
    assert ticket3.status == ApprovalStatus.PENDING
    expired_count = await facade.expire_timed_out_tickets()
    assert expired_count >= 1

    await facade.close()


@pytest.mark.asyncio
async def test_async_facade_get_ticket_by_id_for_all_statuses(tmp_path: Path):
    db_file = tmp_path / "cp_async_get_ticket.db"
    facade = AsyncControlPlaneFacade.from_database_url(f"sqlite+aiosqlite:///{db_file}")

    sid = await facade.open_session("approvals-get-ticket")
    await facade.activate_session(sid)

    async with facade.session_scope() as db:
        proposal_model = ModelRegistry.get("ActionProposal")
        proposal = proposal_model(
            id=uuid4(),
            session_id=sid,
            cycle_event_seq=None,
            resource_id="resource-get-ticket",
            resource_type="task",
            decision=ActionName.STATUS,
            reasoning="needs approval",
            metadata_json={},
            weight=Decimal("1.0"),
            score=Decimal("0.8"),
            action_tier=ActionTier.ALWAYS_APPROVE,
            risk_level=RiskLevel.MEDIUM,
            status=ProposalStatus.PENDING,
        )
        db.add(proposal)
        await db.commit()
        proposal_id = proposal.id

    pending = await facade.create_ticket(sid, proposal_id, datetime.now(UTC) + timedelta(minutes=10))

    approved = await facade.create_ticket(sid, proposal_id, datetime.now(UTC) + timedelta(minutes=10))
    approved = await facade.approve_ticket(approved.id)

    denied = await facade.create_ticket(sid, proposal_id, datetime.now(UTC) + timedelta(minutes=10))
    denied = await facade.deny_ticket(denied.id, reason="manual deny")

    expired = await facade.create_ticket(sid, proposal_id, datetime.now(UTC) - timedelta(minutes=1))
    expired_count = await facade.expire_timed_out_tickets()
    assert expired_count == 1

    assert (await facade.get_ticket(pending.id)) is not None
    assert (await facade.get_ticket(approved.id)) is not None
    assert (await facade.get_ticket(denied.id)) is not None
    assert (await facade.get_ticket(expired.id)) is not None

    pending_fetched = await facade.get_ticket(pending.id)
    approved_fetched = await facade.get_ticket(approved.id)
    denied_fetched = await facade.get_ticket(denied.id)
    expired_fetched = await facade.get_ticket(expired.id)

    assert pending_fetched is not None and pending_fetched.status == ApprovalStatus.PENDING
    assert approved_fetched is not None and approved_fetched.status == ApprovalStatus.APPROVED
    assert denied_fetched is not None and denied_fetched.status == ApprovalStatus.DENIED
    assert expired_fetched is not None and expired_fetched.status == ApprovalStatus.EXPIRED

    assert await facade.get_ticket(uuid4()) is None

    await facade.close()
