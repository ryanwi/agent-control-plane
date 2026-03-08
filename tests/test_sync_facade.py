"""Tests for sync facade APIs and app-event mapping."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from agent_control_plane.models.registry import ModelRegistry
from agent_control_plane.sync import (
    AppEventMapper,
    ControlPlaneFacade,
    DictEventMapper,
    MappedEventDTO,
    SyncControlPlane,
    UnknownAppEventError,
)
from agent_control_plane.types.aliases import (
    AliasProfile,
    AliasRegistry,
    FieldAliasMap,
    apply_inbound_aliases,
    apply_outbound_aliases,
)
from agent_control_plane.types.enums import (
    ActionName,
    ActionTier,
    ApprovalStatus,
    EventKind,
    ProposalStatus,
    RiskLevel,
    UnknownAppEventPolicy,
)
from agent_control_plane.types.proposals import ActionProposalDTO


def _insert_pending_proposal(facade: ControlPlaneFacade, session_id: UUID, *, resource_id: str) -> UUID:
    with facade._cp.session_scope() as db:
        proposal_model = ModelRegistry.get("ActionProposal")
        proposal = proposal_model(
            id=uuid4(),
            session_id=session_id,
            cycle_event_seq=None,
            resource_id=resource_id,
            resource_type="task",
            decision=ActionName.STATUS,
            reasoning="sync projection test",
            metadata_json={},
            weight=Decimal("1.0"),
            score=Decimal("0.9"),
            action_tier=ActionTier.ALWAYS_APPROVE,
            risk_level=RiskLevel.MEDIUM,
            status=ProposalStatus.PENDING,
        )
        db.add(proposal)
        db.commit()
        return proposal.id


def test_sync_control_plane_emit_and_replay_round_trip(tmp_path: Path):
    db_file = tmp_path / "cp_sync_events.db"
    cp = SyncControlPlane(f"sqlite:///{db_file}")
    cp.setup()

    sid = cp.create_session("sync-events", max_cost=Decimal("100"), max_action_count=10)
    seq = cp.emit_event(sid, EventKind.CYCLE_STARTED, {"phase": "begin"}, state_bearing=True)
    assert seq == 1

    events = cp.replay_events(sid)
    assert len(events) == 1
    assert events[0].event_kind == EventKind.CYCLE_STARTED
    assert events[0].payload["phase"] == "begin"
    assert events[0].state_bearing is True
    cp.close()


def test_sync_control_plane_emit_app_event_mapper_and_unknown_policy(tmp_path: Path):
    db_file = tmp_path / "cp_sync_app_events.db"
    cp = SyncControlPlane(f"sqlite:///{db_file}")
    cp.setup()

    sid = cp.create_session("sync-app-events", max_cost=Decimal("100"), max_action_count=10)
    mapper = DictEventMapper({"plan_started": EventKind.CYCLE_STARTED})

    seq = cp.emit_app_event(
        sid,
        "plan_started",
        {"plan_id": "p1"},
        mapper=mapper,
        unknown_policy=UnknownAppEventPolicy.RAISE,
    )
    assert seq == 1

    ignored = cp.emit_app_event(
        sid,
        "unmapped_event",
        {"x": 1},
        mapper=mapper,
        unknown_policy=UnknownAppEventPolicy.IGNORE,
    )
    assert ignored is None

    with pytest.raises(UnknownAppEventError):
        cp.emit_app_event(
            sid,
            "unmapped_event",
            {"x": 2},
            mapper=mapper,
            unknown_policy=UnknownAppEventPolicy.RAISE,
        )

    tagged_seq = cp.emit_app_event(
        sid,
        "plan_started",
        {"plan_id": "p2"},
        mapper=mapper,
        unknown_policy=UnknownAppEventPolicy.RAISE,
        state_bearing=True,
        agent_id="agent-42",
        correlation_id=uuid4(),
        idempotency_key="idem-1",
    )
    assert tagged_seq == 2

    tagged_event = cp.replay_events(sid, after_seq=1)[0]
    assert tagged_event.agent_id == "agent-42"
    assert tagged_event.state_bearing is True
    cp.close()


class _SecurityMapper(AppEventMapper):
    def map_event(self, event_name: str, payload: Mapping[str, Any]) -> MappedEventDTO | None:
        if event_name == "scan_started":
            return DictEventMapper({"scan_started": EventKind.CYCLE_STARTED}).map_event(event_name, payload)
        if event_name == "scan_completed":
            return DictEventMapper({"scan_completed": EventKind.CYCLE_COMPLETED}).map_event(event_name, payload)
        return None


def test_control_plane_facade_session_budget_and_replay(tmp_path: Path):
    db_file = tmp_path / "cp_facade.db"
    facade = ControlPlaneFacade.from_database_url(
        f"sqlite:///{db_file}",
        mapper=_SecurityMapper(),
        unknown_policy=UnknownAppEventPolicy.RAISE,
    )
    facade.setup()

    sid = facade.open_session("facade-demo", max_cost=Decimal("25"), max_action_count=3)
    assert facade.check_budget(sid, cost=Decimal("10"), action_count=1) is True
    facade.increment_budget(sid, cost=Decimal("10"), action_count=1)

    seq = facade.emit_app(sid, "scan_started", {"resource": "host-1"}, state_bearing=True, agent_id="sec-agent")
    assert seq == 1
    close_result = facade.close_session(sid)
    assert close_result.events_appended == 0
    assert close_result.session.status.value == "completed"

    events = facade.replay(sid)
    assert len(events) == 1
    assert events[0].event_kind == EventKind.CYCLE_STARTED
    assert events[0].state_bearing is True

    emitted = facade.emit(
        sid,
        EventKind.CYCLE_COMPLETED,
        {"done": True},
        state_bearing=True,
        agent_id="sec-agent",
    )
    assert emitted == 2

    sid2 = facade.open_session("abort-demo", max_cost=Decimal("5"), max_action_count=1)
    abort_result = facade.abort_session(sid2, reason="operator stop")
    assert abort_result.session.status.value == "aborted"
    facade.close()


def test_control_plane_facade_command_id_idempotency(tmp_path: Path):
    db_file = tmp_path / "cp_facade_idempotency.db"
    facade = ControlPlaneFacade.from_database_url(f"sqlite:///{db_file}")
    facade.setup()

    sid = facade.open_session("idempotency-demo", command_id="sync-open-1")
    sid_again = facade.open_session("ignored-name", command_id="sync-open-1")
    assert sid_again == sid

    seq1 = facade.emit(sid, EventKind.CYCLE_STARTED, {"phase": "one"}, command_id="sync-emit-1")
    seq2 = facade.emit(sid, EventKind.CYCLE_STARTED, {"phase": "two"}, command_id="sync-emit-1")
    assert seq2 == seq1

    close1 = facade.close_session(sid, command_id="sync-close-1")
    close2 = facade.close_session(sid, command_id="sync-close-1")
    assert close1.session.status.value == "completed"
    assert close2.session.status.value == "completed"

    sid_kill = facade.open_session("kill-target")
    kill1 = facade.kill_session(sid_kill, command_id="sync-kill-1")
    kill2 = facade.kill_session(sid_kill, command_id="sync-kill-1")
    assert kill1.scope == kill2.scope
    assert kill1.session_id == kill2.session_id
    assert kill1.tickets_denied == kill2.tickets_denied

    facade.close()


def test_control_plane_facade_approval_flows_and_idempotency(tmp_path: Path):
    db_file = tmp_path / "cp_facade_approvals.db"
    facade = ControlPlaneFacade.from_database_url(f"sqlite:///{db_file}")
    facade.setup()

    sid = facade.open_session("sync-approvals")
    proposal_id = _insert_pending_proposal(facade, sid, resource_id="sync-asset-1")

    timeout_at = datetime.now(UTC) + timedelta(minutes=10)
    ticket = facade.create_ticket(sid, proposal_id, timeout_at, command_id="sync-ticket-create-1")
    ticket_again = facade.create_ticket(sid, proposal_id, timeout_at, command_id="sync-ticket-create-1")
    assert ticket_again.id == ticket.id
    assert ticket_again.status == ApprovalStatus.PENDING

    approved = facade.approve_ticket(
        ticket.id,
        reason="sync approve",
        command_id="sync-ticket-approve-1",
    )
    approved_again = facade.approve_ticket(
        ticket.id,
        reason="ignored",
        command_id="sync-ticket-approve-1",
    )
    assert approved.status == ApprovalStatus.APPROVED
    assert approved_again.status == ApprovalStatus.APPROVED

    approved_proposal = facade.get_proposal(proposal_id)
    assert approved_proposal is not None
    assert approved_proposal.status == ProposalStatus.APPROVED

    proposal_id_2 = _insert_pending_proposal(facade, sid, resource_id="sync-asset-2")
    ticket_2 = facade.create_ticket(sid, proposal_id_2, datetime.now(UTC) + timedelta(minutes=5))
    denied = facade.deny_ticket(ticket_2.id, reason="sync deny", command_id="sync-ticket-deny-1")
    denied_again = facade.deny_ticket(ticket_2.id, reason="ignored", command_id="sync-ticket-deny-1")
    assert denied.status == ApprovalStatus.DENIED
    assert denied_again.status == ApprovalStatus.DENIED

    denied_proposal = facade.get_proposal(proposal_id_2)
    assert denied_proposal is not None
    assert denied_proposal.status == ProposalStatus.DENIED

    facade.close()


def test_control_plane_facade_create_proposal_idempotency(tmp_path: Path):
    db_file = tmp_path / "cp_facade_create_proposal.db"
    facade = ControlPlaneFacade.from_database_url(f"sqlite:///{db_file}")
    facade.setup()

    sid = facade.open_session("sync-create-proposal")
    proposal = ActionProposalDTO(
        session_id=sid,
        resource_id="sync-resource-1",
        resource_type="task",
        decision=ActionName.STATUS,
        reasoning="create proposal test",
        weight=Decimal("1.0"),
        score=Decimal("0.8"),
    )

    created = facade.create_proposal(proposal, command_id="sync-create-proposal-1")
    replayed = facade.create_proposal(proposal, command_id="sync-create-proposal-1")
    assert replayed.id == created.id

    loaded = facade.get_proposal(created.id)
    assert loaded is not None
    assert loaded.id == created.id
    assert loaded.resource_id == "sync-resource-1"

    with pytest.raises(ValueError, match="already used for operation"):
        facade.create_ticket(
            sid,
            created.id,
            datetime.now(UTC) + timedelta(minutes=5),
            command_id="sync-create-proposal-1",
        )

    facade.close()


def test_control_plane_facade_state_feed_projection_end_to_end(tmp_path: Path):
    db_file = tmp_path / "cp_sync_projection_e2e.db"
    facade = ControlPlaneFacade.from_database_url(f"sqlite:///{db_file}")
    facade.setup()

    sid = facade.open_session("sync-projection")
    proposal_id = _insert_pending_proposal(facade, sid, resource_id="projection-sync-asset-1")
    ticket = facade.create_ticket(sid, proposal_id, datetime.now(UTC) + timedelta(minutes=10))

    facade.emit(sid, EventKind.CYCLE_STARTED, {"phase": "start"}, state_bearing=True)
    facade.approve_ticket(ticket.id, reason="projection approve")
    facade.emit(sid, EventKind.CYCLE_COMPLETED, {"phase": "done"}, state_bearing=True)

    projection_tickets: dict[UUID, ApprovalStatus] = {}
    projection_proposals: dict[UUID, ProposalStatus] = {}
    cursor = 0

    while True:
        feed = facade.get_state_change_feed(cursor=cursor, limit=10)
        if not feed.items:
            break
        for item in feed.items:
            session_id = item.event.session_id
            tickets_page = facade.list_tickets(session_id=session_id, limit=200, offset=0)
            for projected_ticket in tickets_page.items:
                projection_tickets[projected_ticket.id] = projected_ticket.status

            proposals_page = facade.list_proposals(session_id=session_id, limit=200, offset=0)
            for projected_proposal in proposals_page.items:
                projection_proposals[projected_proposal.id] = projected_proposal.status

            cursor = item.cursor

    canonical_ticket = facade.get_ticket(ticket.id)
    canonical_proposal = facade.get_proposal(proposal_id)

    assert canonical_ticket is not None
    assert canonical_proposal is not None
    assert projection_tickets[ticket.id] == canonical_ticket.status
    assert projection_proposals[proposal_id] == canonical_proposal.status
    assert projection_tickets[ticket.id] == ApprovalStatus.APPROVED
    assert projection_proposals[proposal_id] == ProposalStatus.APPROVED

    facade.close()


def test_alias_helpers_in_projection_workflow():
    AliasRegistry.clear_profiles()
    profile = AliasProfile(
        name="workflow",
        aliases=FieldAliasMap(
            canonical_to_alias={
                "resource_id": "resourceId",
                "state_bearing": "stateBearing",
                "event_kind": "eventKind",
            }
        ),
    )
    AliasRegistry.register_profile(profile)

    inbound = apply_inbound_aliases({"resourceId": "asset-9", "stateBearing": True}, "workflow")
    assert inbound == {"resource_id": "asset-9", "state_bearing": True}

    outbound = apply_outbound_aliases(
        {"event_kind": EventKind.CYCLE_STARTED.value, "state_bearing": True, "resource_id": "asset-9"},
        "workflow",
    )
    assert outbound == {"eventKind": "cycle_started", "stateBearing": True, "resourceId": "asset-9"}

    AliasRegistry.clear_profiles()
