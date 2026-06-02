"""Tests for AsyncControlPlaneFacade."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from agent_control_plane.async_facade import AsyncControlPlaneFacade
from agent_control_plane.engine.concurrency import CycleAlreadyActiveError
from agent_control_plane.engine.state_integrity import SessionStateIntegrityError
from agent_control_plane.models.registry import ModelRegistry
from agent_control_plane.sync import DictEventMapper
from agent_control_plane.types.approvals import ApprovalScope
from agent_control_plane.types.enums import (
    ActionName,
    ActionTier,
    ApprovalDecisionType,
    ApprovalStatus,
    EvaluationDecision,
    EventKind,
    GuardrailPhase,
    ProposalStatus,
    RiskLevel,
    SessionStatus,
    UnknownAppEventPolicy,
)
from agent_control_plane.types.frames import EmitMetadata
from agent_control_plane.types.proposals import ActionProposal


@pytest.mark.asyncio
async def test_async_facade_session_budget_emit_and_close(tmp_path: Path):
    db_file = tmp_path / "cp_async_facade.db"
    facade = AsyncControlPlaneFacade.from_database_url(
        f"sqlite+aiosqlite:///{db_file}",
        mapper=DictEventMapper({"started": EventKind.CYCLE_STARTED}),
        unknown_policy=UnknownAppEventPolicy.RAISE,
    )

    sid = await facade.sessions.open_session("async-demo", max_cost=Decimal("20"), max_action_count=2)
    assert await facade.budget.check_budget(sid, cost=Decimal("5"), action_count=1) is True
    await facade.budget.increment_budget(sid, cost=Decimal("5"), action_count=1)

    seq = await facade.sessions.emit_app(sid, "started", {"k": "v"}, state_bearing=True, agent_id="agent-a")
    assert seq == 1

    close_result = await facade.sessions.close_session(sid)
    assert close_result.session.status == SessionStatus.COMPLETED
    assert close_result.events_appended == 0

    events = await facade.sessions.replay(sid)
    assert len(events) == 1
    assert events[0].state_bearing is True

    await facade.close()


@pytest.mark.asyncio
async def test_async_facade_session_transitions_and_cycle_lock(tmp_path: Path):
    db_file = tmp_path / "cp_async_transitions.db"
    facade = AsyncControlPlaneFacade.from_database_url(f"sqlite+aiosqlite:///{db_file}")

    sid = await facade.sessions.open_session("transitions")
    listed = await facade.observer.list_sessions(statuses=[SessionStatus.CREATED])
    assert any(s.id == sid for s in listed)

    activated = await facade.lifecycle.activate_session(sid)
    assert activated.session.status == SessionStatus.ACTIVE

    cycle_id = uuid4()
    await facade.lifecycle.acquire_cycle(sid, cycle_id)
    with pytest.raises(CycleAlreadyActiveError):
        await facade.lifecycle.acquire_cycle(sid, uuid4())
    await facade.lifecycle.release_cycle(sid)

    paused = await facade.lifecycle.pause_session(sid)
    assert paused.session.status == SessionStatus.PAUSED

    resumed = await facade.lifecycle.resume_session(sid)
    assert resumed.session.status == SessionStatus.ACTIVE

    await facade.close()


@pytest.mark.asyncio
async def test_async_facade_resume_fails_closed_on_corrupt_state(tmp_path: Path):
    db_file = tmp_path / "cp_async_integrity.db"
    facade = AsyncControlPlaneFacade.from_database_url(f"sqlite+aiosqlite:///{db_file}")

    sid = await facade.sessions.open_session("integrity-test")
    await facade.lifecycle.activate_session(sid)
    await facade.lifecycle.pause_session(sid)

    # Corrupt persisted state by writing a negative used_cost directly.
    async with facade.session_scope() as db:
        session_model = ModelRegistry.get("ControlSession")
        row = await db.get(session_model, sid)
        row.used_cost = Decimal("-5")
        await db.commit()

    with pytest.raises(SessionStateIntegrityError):
        await facade.lifecycle.resume_session(sid)

    # Session must stay PAUSED — fail closed.
    session = await facade.sessions.get_session(sid)
    assert session is not None
    assert session.status == SessionStatus.PAUSED

    # State-bearing audit event must be recorded.
    events = await facade.sessions.replay(sid)
    invalid = [e for e in events if e.kind == EventKind.SESSION_STATE_INVALID]
    assert invalid
    assert "negative_used_cost" in str(invalid[-1].payload)

    await facade.close()


@pytest.mark.asyncio
async def test_async_facade_activate_fails_closed_on_corrupt_state(tmp_path: Path):
    db_file = tmp_path / "cp_async_activate_integrity.db"
    facade = AsyncControlPlaneFacade.from_database_url(f"sqlite+aiosqlite:///{db_file}")

    sid = await facade.sessions.open_session("activate-integrity")  # CREATED

    # Corrupt persisted state before the CREATED -> ACTIVE transition.
    async with facade.session_scope() as db:
        session_model = ModelRegistry.get("ControlSession")
        row = await db.get(session_model, sid)
        row.used_cost = Decimal("-5")
        await db.commit()

    with pytest.raises(SessionStateIntegrityError):
        await facade.lifecycle.activate_session(sid)

    # Session must stay CREATED — fail closed.
    session = await facade.sessions.get_session(sid)
    assert session is not None
    assert session.status == SessionStatus.CREATED

    events = await facade.sessions.replay(sid)
    invalid = [e for e in events if e.kind == EventKind.SESSION_STATE_INVALID]
    assert invalid
    assert "negative_used_cost" in str(invalid[-1].payload)

    await facade.close()


@pytest.mark.asyncio
async def test_async_facade_approval_lifecycle_and_expiry(tmp_path: Path):
    db_file = tmp_path / "cp_async_approvals.db"
    facade = AsyncControlPlaneFacade.from_database_url(f"sqlite+aiosqlite:///{db_file}")

    sid = await facade.sessions.open_session("approvals")
    await facade.lifecycle.activate_session(sid)

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

    ticket = await facade.approvals.create_ticket(sid, proposal_id, datetime.now(UTC) + timedelta(minutes=5))
    approved = await facade.approvals.approve_ticket(
        ticket.id,
        decision_type=ApprovalDecisionType.ALLOW_FOR_SESSION,
        scope=ApprovalScope(resource_ids=["resource-1"], max_cost=Decimal("100"), max_count=2),
    )
    assert approved.status == ApprovalStatus.APPROVED
    assert approved.decision_type == ApprovalDecisionType.ALLOW_FOR_SESSION

    async with facade.session_scope() as db:
        proposal_model = ModelRegistry.get("ActionProposal")
        row = await db.get(proposal_model, proposal_id)
        assert row.status == ProposalStatus.APPROVED

    ticket2 = await facade.approvals.create_ticket(sid, proposal_id, datetime.now(UTC) + timedelta(minutes=5))
    denied = await facade.approvals.deny_ticket(ticket2.id, reason="manual deny")
    assert denied.status == ApprovalStatus.DENIED

    ticket3 = await facade.approvals.create_ticket(sid, proposal_id, datetime.now(UTC) - timedelta(minutes=1))
    assert ticket3.status == ApprovalStatus.PENDING
    expired_count = await facade.approvals.expire_timed_out_tickets()
    assert expired_count >= 1

    await facade.close()


@pytest.mark.asyncio
async def test_async_facade_get_ticket_by_id_for_all_statuses(tmp_path: Path):
    db_file = tmp_path / "cp_async_get_ticket.db"
    facade = AsyncControlPlaneFacade.from_database_url(f"sqlite+aiosqlite:///{db_file}")

    sid = await facade.sessions.open_session("approvals-get-ticket")
    await facade.lifecycle.activate_session(sid)

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

    pending = await facade.approvals.create_ticket(sid, proposal_id, datetime.now(UTC) + timedelta(minutes=10))

    approved = await facade.approvals.create_ticket(sid, proposal_id, datetime.now(UTC) + timedelta(minutes=10))
    approved = await facade.approvals.approve_ticket(approved.id)

    denied = await facade.approvals.create_ticket(sid, proposal_id, datetime.now(UTC) + timedelta(minutes=10))
    denied = await facade.approvals.deny_ticket(denied.id, reason="manual deny")

    expired = await facade.approvals.create_ticket(sid, proposal_id, datetime.now(UTC) - timedelta(minutes=1))
    expired_count = await facade.approvals.expire_timed_out_tickets()
    assert expired_count == 1

    assert (await facade.approvals.get_ticket(pending.id)) is not None
    assert (await facade.approvals.get_ticket(approved.id)) is not None
    assert (await facade.approvals.get_ticket(denied.id)) is not None
    assert (await facade.approvals.get_ticket(expired.id)) is not None

    pending_fetched = await facade.approvals.get_ticket(pending.id)
    approved_fetched = await facade.approvals.get_ticket(approved.id)
    denied_fetched = await facade.approvals.get_ticket(denied.id)
    expired_fetched = await facade.approvals.get_ticket(expired.id)

    assert pending_fetched is not None and pending_fetched.status == ApprovalStatus.PENDING
    assert approved_fetched is not None and approved_fetched.status == ApprovalStatus.APPROVED
    assert denied_fetched is not None and denied_fetched.status == ApprovalStatus.DENIED
    assert expired_fetched is not None and expired_fetched.status == ApprovalStatus.EXPIRED

    assert await facade.approvals.get_ticket(uuid4()) is None

    await facade.close()


@pytest.mark.asyncio
async def test_async_facade_create_proposal_idempotency(tmp_path: Path):
    db_file = tmp_path / "cp_async_create_proposal.db"
    facade = AsyncControlPlaneFacade.from_database_url(f"sqlite+aiosqlite:///{db_file}")

    sid = await facade.sessions.open_session("async-create-proposal")
    proposal = ActionProposal(
        session_id=sid,
        resource_id="async-resource-1",
        resource_type="task",
        decision=ActionName.STATUS,
        reasoning="create proposal test",
        weight=Decimal("1.0"),
        score=Decimal("0.8"),
    )

    created = await facade.approvals.create_proposal(proposal, command_id="async-create-proposal-1")
    replayed = await facade.approvals.create_proposal(proposal, command_id="async-create-proposal-1")
    assert replayed.id == created.id

    second = await facade.approvals.create_proposal(
        proposal.model_copy(update={"id": uuid4(), "resource_id": "async-resource-2"}),
        command_id="async-create-proposal-2",
    )
    assert second.id != created.id

    loaded = await facade.approvals.get_proposal(created.id)
    assert loaded is not None
    assert loaded.id == created.id
    assert loaded.resource_id == "async-resource-1"

    with pytest.raises(ValueError, match="already used for operation"):
        await facade.approvals.create_ticket(
            sid,
            created.id,
            datetime.now(UTC) + timedelta(minutes=5),
            command_id="async-create-proposal-1",
        )

    await facade.close()


@pytest.mark.asyncio
async def test_async_facade_operational_scorecard_enriched_fields(tmp_path: Path):
    db_file = tmp_path / "cp_async_scorecard.db"
    facade = AsyncControlPlaneFacade.from_database_url(f"sqlite+aiosqlite:///{db_file}")

    sid = await facade.sessions.open_session("async-scorecard")

    await facade.agentic.record_evaluation(
        sid,
        operation="approve_ticket",
        decision=EvaluationDecision.BLOCK,
        score=0.2,
        reasons=["policy mismatch"],
    )
    await facade.agentic.apply_guardrail(
        sid,
        phase=GuardrailPhase.INPUT,
        allow=True,
        policy_code="CP-GR-ALLOW",
        reason="safe",
    )
    await facade.agentic.apply_guardrail(
        sid,
        phase=GuardrailPhase.OUTPUT,
        allow=False,
        policy_code="CP-GR-DENY",
        reason="unsafe",
    )
    await facade.agentic.request_handoff(
        sid,
        source_agent_id="agent-a",
        target_agent_id="agent-b",
        allowed_actions=["status"],
        accepted=True,
    )
    await facade.agentic.request_handoff(
        sid,
        source_agent_id="agent-a",
        target_agent_id="agent-c",
        allowed_actions=["status"],
        accepted=False,
    )

    await facade.sessions.emit(sid, EventKind.APPROVAL_REQUESTED, {}, state_bearing=False)
    await facade.sessions.emit(sid, EventKind.APPROVAL_GRANTED, {}, state_bearing=False)
    await facade.sessions.emit(sid, EventKind.APPROVAL_DENIED, {}, state_bearing=False)
    await facade.sessions.emit(sid, EventKind.CHECKPOINT_CREATED, {}, state_bearing=False)
    await facade.sessions.emit(sid, EventKind.ROLLBACK_COMPLETED, {}, state_bearing=False)
    await facade.sessions.emit(sid, EventKind.EXECUTION_COMPLETED, {"cost": 2.5}, state_bearing=False)
    await facade.sessions.emit(sid, EventKind.BUDGET_EXHAUSTED, {}, state_bearing=False)
    await facade.sessions.emit(sid, EventKind.KILL_SWITCH_TRIGGERED, {"reason": "budget_denied"}, state_bearing=False)

    scorecard = await facade.observer.get_operational_scorecard(session_id=sid)
    assert scorecard.evaluations_blocked == 1
    assert scorecard.evaluation_block_reasons.get("policy mismatch") == 1
    assert scorecard.guardrail_denies == 1
    assert scorecard.guardrail_allows == 1
    assert scorecard.guardrail_policy_code_counts.get("CP-GR-ALLOW") == 1
    assert scorecard.guardrail_policy_code_counts.get("CP-GR-DENY") == 1
    assert scorecard.handoffs_accepted == 1
    assert scorecard.handoffs_rejected == 1
    assert scorecard.handoff_accept_rate == 0.5
    assert scorecard.budget_denied_count == 1
    assert scorecard.budget_exhausted_count == 1
    assert scorecard.approval_latency_ms_p50 is not None
    assert scorecard.approval_latency_ms_p95 is not None
    assert scorecard.checkpoint_rollback_latency_ms_p50 is not None
    assert scorecard.checkpoint_rollback_latency_ms_p95 is not None
    assert scorecard.avg_cost_per_successful_action == 2.5
    # Approval-fatigue signal: grant/deny counts and the derived grant rate.
    assert scorecard.approvals_granted == 1
    assert scorecard.approvals_denied == 1
    assert scorecard.approval_grant_rate == 0.5

    await facade.close()


@pytest.mark.asyncio
async def test_async_facade_read_models_feed_health_and_idempotency(tmp_path: Path):
    db_file = tmp_path / "cp_async_facade_reads.db"
    facade = AsyncControlPlaneFacade.from_database_url(f"sqlite+aiosqlite:///{db_file}")

    session_command_id = "cmd-open-session-1"
    sid = await facade.sessions.open_session("read-models", command_id=session_command_id)
    sid_again = await facade.sessions.open_session("read-models-ignored", command_id=session_command_id)
    assert sid_again == sid

    await facade.lifecycle.activate_session(sid)

    async with facade.session_scope() as db:
        proposal_model = ModelRegistry.get("ActionProposal")
        proposal = proposal_model(
            id=uuid4(),
            session_id=sid,
            cycle_event_seq=None,
            resource_id="resource-feed",
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

    ticket_command_id = "cmd-create-ticket-1"
    ticket = await facade.approvals.create_ticket(
        sid,
        proposal_id,
        datetime.now(UTC) + timedelta(minutes=10),
        command_id=ticket_command_id,
    )
    ticket_again = await facade.approvals.create_ticket(
        sid,
        proposal_id,
        datetime.now(UTC) + timedelta(minutes=10),
        command_id=ticket_command_id,
    )
    assert ticket_again.id == ticket.id

    proposal = await facade.approvals.get_proposal(proposal_id)
    assert proposal is not None
    assert proposal.id == proposal_id

    proposal_page = await facade.approvals.list_proposals(
        session_id=sid, statuses=[ProposalStatus.PENDING], limit=10, offset=0
    )
    assert len(proposal_page.items) == 1
    assert proposal_page.items[0].id == proposal_id

    ticket_page = await facade.approvals.list_tickets(
        session_id=sid, statuses=[ApprovalStatus.PENDING], limit=10, offset=0
    )
    assert len(ticket_page.items) == 1
    assert ticket_page.items[0].id == ticket.id

    await facade.sessions.emit(sid, EventKind.CYCLE_STARTED, {"phase": "a"}, state_bearing=True)
    await facade.sessions.emit(sid, EventKind.CYCLE_COMPLETED, {"phase": "b"}, state_bearing=False)

    feed = await facade.observer.get_state_change_feed(session_id=sid, cursor=0, limit=10)
    assert len(feed.items) == 1
    assert feed.items[0].event.kind == EventKind.CYCLE_STARTED

    health = await facade.observer.get_health_snapshot()
    assert health.active_sessions >= 1
    assert health.pending_tickets >= 1

    emit_command_id = "cmd-emit-1"
    seq1 = await facade.sessions.emit(
        sid,
        EventKind.CYCLE_STARTED,
        {"phase": "c"},
        state_bearing=True,
        metadata=EmitMetadata(command_id=emit_command_id),
    )
    seq2 = await facade.sessions.emit(
        sid,
        EventKind.CYCLE_STARTED,
        {"phase": "ignored"},
        state_bearing=True,
        metadata=EmitMetadata(command_id=emit_command_id),
    )
    assert seq2 == seq1

    close_command_id = "cmd-close-1"
    closed1 = await facade.sessions.close_session(sid, command_id=close_command_id)
    closed2 = await facade.sessions.close_session(sid, command_id=close_command_id)
    assert closed1.session.status == SessionStatus.COMPLETED
    assert closed2.session.status == SessionStatus.COMPLETED

    await facade.close()


@pytest.mark.asyncio
async def test_async_facade_state_feed_projection_end_to_end(tmp_path: Path):
    db_file = tmp_path / "cp_async_projection_e2e.db"
    facade = AsyncControlPlaneFacade.from_database_url(f"sqlite+aiosqlite:///{db_file}")

    sid = await facade.sessions.open_session("projection-e2e")
    await facade.lifecycle.activate_session(sid)

    async with facade.session_scope() as db:
        proposal_model = ModelRegistry.get("ActionProposal")
        proposal = proposal_model(
            id=uuid4(),
            session_id=sid,
            cycle_event_seq=None,
            resource_id="projection-asset-1",
            resource_type="task",
            decision=ActionName.STATUS,
            reasoning="projection test",
            metadata_json={},
            weight=Decimal("1.0"),
            score=Decimal("0.9"),
            action_tier=ActionTier.ALWAYS_APPROVE,
            risk_level=RiskLevel.MEDIUM,
            status=ProposalStatus.PENDING,
        )
        db.add(proposal)
        await db.commit()
        proposal_id = proposal.id

    ticket = await facade.approvals.create_ticket(sid, proposal_id, datetime.now(UTC) + timedelta(minutes=10))
    await facade.sessions.emit(sid, EventKind.CYCLE_STARTED, {"phase": "start"}, state_bearing=True)
    await facade.approvals.approve_ticket(ticket.id, reason="projection approve")
    await facade.sessions.emit(sid, EventKind.CYCLE_COMPLETED, {"phase": "done"}, state_bearing=True)

    projection_tickets: dict = {}
    projection_proposals: dict = {}
    cursor = 0

    while True:
        feed = await facade.observer.get_state_change_feed(cursor=cursor, limit=10)
        if not feed.items:
            break

        for item in feed.items:
            session_id = item.event.session_id
            tickets_page = await facade.approvals.list_tickets(session_id=session_id, limit=200, offset=0)
            for projected_ticket in tickets_page.items:
                projection_tickets[projected_ticket.id] = projected_ticket.status

            proposals_page = await facade.approvals.list_proposals(session_id=session_id, limit=200, offset=0)
            for projected_proposal in proposals_page.items:
                projection_proposals[projected_proposal.id] = projected_proposal.status

            cursor = item.cursor

    canonical_ticket = await facade.approvals.get_ticket(ticket.id)
    canonical_proposal = await facade.approvals.get_proposal(proposal_id)

    assert canonical_ticket is not None
    assert canonical_proposal is not None
    assert projection_tickets[ticket.id] == canonical_ticket.status
    assert projection_proposals[proposal_id] == canonical_proposal.status
    assert projection_tickets[ticket.id] == ApprovalStatus.APPROVED
    assert projection_proposals[proposal_id] == ProposalStatus.APPROVED

    await facade.close()
