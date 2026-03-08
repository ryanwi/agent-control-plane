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
    EvaluationDecision,
    EventKind,
    GuardrailPhase,
    ProposalStatus,
    RiskLevel,
    SessionStatus,
    UnknownAppEventPolicy,
)
from agent_control_plane.types.proposals import ActionProposalDTO


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


@pytest.mark.asyncio
async def test_async_facade_create_proposal_idempotency(tmp_path: Path):
    db_file = tmp_path / "cp_async_create_proposal.db"
    facade = AsyncControlPlaneFacade.from_database_url(f"sqlite+aiosqlite:///{db_file}")

    sid = await facade.open_session("async-create-proposal")
    proposal = ActionProposalDTO(
        session_id=sid,
        resource_id="async-resource-1",
        resource_type="task",
        decision=ActionName.STATUS,
        reasoning="create proposal test",
        weight=Decimal("1.0"),
        score=Decimal("0.8"),
    )

    created = await facade.create_proposal(proposal, command_id="async-create-proposal-1")
    replayed = await facade.create_proposal(proposal, command_id="async-create-proposal-1")
    assert replayed.id == created.id

    loaded = await facade.get_proposal(created.id)
    assert loaded is not None
    assert loaded.id == created.id
    assert loaded.resource_id == "async-resource-1"

    with pytest.raises(ValueError, match="already used for operation"):
        await facade.create_ticket(
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

    sid = await facade.open_session("async-scorecard")

    await facade.record_evaluation(
        sid,
        operation="approve_ticket",
        decision=EvaluationDecision.BLOCK,
        score=0.2,
        reasons=["policy mismatch"],
    )
    await facade.apply_guardrail(
        sid,
        phase=GuardrailPhase.INPUT,
        allow=True,
        policy_code="CP-GR-ALLOW",
        reason="safe",
    )
    await facade.apply_guardrail(
        sid,
        phase=GuardrailPhase.OUTPUT,
        allow=False,
        policy_code="CP-GR-DENY",
        reason="unsafe",
    )
    await facade.request_handoff(
        sid,
        source_agent_id="agent-a",
        target_agent_id="agent-b",
        allowed_actions=["status"],
        accepted=True,
    )
    await facade.request_handoff(
        sid,
        source_agent_id="agent-a",
        target_agent_id="agent-c",
        allowed_actions=["status"],
        accepted=False,
    )

    await facade.emit(sid, EventKind.APPROVAL_REQUESTED, {}, state_bearing=False)
    await facade.emit(sid, EventKind.APPROVAL_GRANTED, {}, state_bearing=False)
    await facade.emit(sid, EventKind.CHECKPOINT_CREATED, {}, state_bearing=False)
    await facade.emit(sid, EventKind.ROLLBACK_COMPLETED, {}, state_bearing=False)
    await facade.emit(sid, EventKind.EXECUTION_COMPLETED, {"cost": 2.5}, state_bearing=False)
    await facade.emit(sid, EventKind.BUDGET_EXHAUSTED, {}, state_bearing=False)
    await facade.emit(sid, EventKind.KILL_SWITCH_TRIGGERED, {"reason": "budget_denied"}, state_bearing=False)

    scorecard = await facade.get_operational_scorecard(session_id=sid)
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

    await facade.close()


@pytest.mark.asyncio
async def test_async_facade_read_models_feed_health_and_idempotency(tmp_path: Path):
    db_file = tmp_path / "cp_async_facade_reads.db"
    facade = AsyncControlPlaneFacade.from_database_url(f"sqlite+aiosqlite:///{db_file}")

    session_command_id = "cmd-open-session-1"
    sid = await facade.open_session("read-models", command_id=session_command_id)
    sid_again = await facade.open_session("read-models-ignored", command_id=session_command_id)
    assert sid_again == sid

    await facade.activate_session(sid)

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
    ticket = await facade.create_ticket(
        sid,
        proposal_id,
        datetime.now(UTC) + timedelta(minutes=10),
        command_id=ticket_command_id,
    )
    ticket_again = await facade.create_ticket(
        sid,
        proposal_id,
        datetime.now(UTC) + timedelta(minutes=10),
        command_id=ticket_command_id,
    )
    assert ticket_again.id == ticket.id

    proposal = await facade.get_proposal(proposal_id)
    assert proposal is not None
    assert proposal.id == proposal_id

    proposal_page = await facade.list_proposals(session_id=sid, statuses=[ProposalStatus.PENDING], limit=10, offset=0)
    assert len(proposal_page.items) == 1
    assert proposal_page.items[0].id == proposal_id

    ticket_page = await facade.list_tickets(session_id=sid, statuses=[ApprovalStatus.PENDING], limit=10, offset=0)
    assert len(ticket_page.items) == 1
    assert ticket_page.items[0].id == ticket.id

    await facade.emit(sid, EventKind.CYCLE_STARTED, {"phase": "a"}, state_bearing=True)
    await facade.emit(sid, EventKind.CYCLE_COMPLETED, {"phase": "b"}, state_bearing=False)

    feed = await facade.get_state_change_feed(session_id=sid, cursor=0, limit=10)
    assert len(feed.items) == 1
    assert feed.items[0].event.event_kind == EventKind.CYCLE_STARTED

    health = await facade.get_health_snapshot()
    assert health.active_sessions >= 1
    assert health.pending_tickets >= 1

    emit_command_id = "cmd-emit-1"
    seq1 = await facade.emit(
        sid,
        EventKind.CYCLE_STARTED,
        {"phase": "c"},
        state_bearing=True,
        command_id=emit_command_id,
    )
    seq2 = await facade.emit(
        sid,
        EventKind.CYCLE_STARTED,
        {"phase": "ignored"},
        state_bearing=True,
        command_id=emit_command_id,
    )
    assert seq2 == seq1

    close_command_id = "cmd-close-1"
    closed1 = await facade.close_session(sid, command_id=close_command_id)
    closed2 = await facade.close_session(sid, command_id=close_command_id)
    assert closed1.session.status == SessionStatus.COMPLETED
    assert closed2.session.status == SessionStatus.COMPLETED

    await facade.close()


@pytest.mark.asyncio
async def test_async_facade_state_feed_projection_end_to_end(tmp_path: Path):
    db_file = tmp_path / "cp_async_projection_e2e.db"
    facade = AsyncControlPlaneFacade.from_database_url(f"sqlite+aiosqlite:///{db_file}")

    sid = await facade.open_session("projection-e2e")
    await facade.activate_session(sid)

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

    ticket = await facade.create_ticket(sid, proposal_id, datetime.now(UTC) + timedelta(minutes=10))
    await facade.emit(sid, EventKind.CYCLE_STARTED, {"phase": "start"}, state_bearing=True)
    await facade.approve_ticket(ticket.id, reason="projection approve")
    await facade.emit(sid, EventKind.CYCLE_COMPLETED, {"phase": "done"}, state_bearing=True)

    projection_tickets: dict = {}
    projection_proposals: dict = {}
    cursor = 0

    while True:
        feed = await facade.get_state_change_feed(cursor=cursor, limit=10)
        if not feed.items:
            break

        for item in feed.items:
            session_id = item.event.session_id
            tickets_page = await facade.list_tickets(session_id=session_id, limit=200, offset=0)
            for projected_ticket in tickets_page.items:
                projection_tickets[projected_ticket.id] = projected_ticket.status

            proposals_page = await facade.list_proposals(session_id=session_id, limit=200, offset=0)
            for projected_proposal in proposals_page.items:
                projection_proposals[projected_proposal.id] = projected_proposal.status

            cursor = item.cursor

    canonical_ticket = await facade.get_ticket(ticket.id)
    canonical_proposal = await facade.get_proposal(proposal_id)

    assert canonical_ticket is not None
    assert canonical_proposal is not None
    assert projection_tickets[ticket.id] == canonical_ticket.status
    assert projection_proposals[proposal_id] == canonical_proposal.status
    assert projection_tickets[ticket.id] == ApprovalStatus.APPROVED
    assert projection_proposals[proposal_id] == ProposalStatus.APPROVED

    await facade.close()
