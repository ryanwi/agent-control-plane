"""Tests for sync facade APIs and app-event mapping."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from agent_control_plane.engine.session_risk_accumulator import SessionRiskAccumulator
from agent_control_plane.models.registry import ModelRegistry
from agent_control_plane.sync import (
    AppEventMapper,
    ControlPlaneFacade,
    DictEventMapper,
    MappedEvent,
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
    ActionTier,
    ApprovalStatus,
    EventKind,
    ExecutionMode,
    ProposalStatus,
    RiskLevel,
    SessionStatus,
    UnknownAppEventPolicy,
)
from agent_control_plane.types.frames import EmitMetadata, EventMetadata
from agent_control_plane.types.policies import PolicySnapshot
from agent_control_plane.types.preconditions import Precondition, PreconditionStatus
from agent_control_plane.types.proposals import ActionProposal
from agent_control_plane.types.risk import RiskPattern


def _insert_pending_proposal(facade: ControlPlaneFacade, session_id: UUID, *, resource_id: str) -> UUID:
    with facade._cp.session_scope() as db:
        proposal_model = ModelRegistry.get("ActionProposal")
        proposal = proposal_model(
            id=uuid4(),
            session_id=session_id,
            cycle_event_seq=None,
            resource_id=resource_id,
            resource_type="task",
            decision="status",
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
    assert events[0].kind == EventKind.CYCLE_STARTED
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
        metadata=EventMetadata(agent_id="agent-42", correlation_id=uuid4(), idempotency_key="idem-1"),
    )
    assert tagged_seq == 2

    tagged_event = cp.replay_events(sid, after_seq=1)[0]
    assert tagged_event.agent_id == "agent-42"
    assert tagged_event.state_bearing is True
    cp.close()


class _SecurityMapper(AppEventMapper):
    def map_event(self, event_name: str, payload: Mapping[str, Any]) -> MappedEvent | None:
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

    sid = facade.sessions.open_session("facade-demo", max_cost=Decimal("25"), max_action_count=3)
    assert facade.budget.check_budget(sid, cost=Decimal("10"), action_count=1) is True
    facade.budget.increment_budget(sid, cost=Decimal("10"), action_count=1)

    seq = facade.sessions.emit_app(
        sid, "scan_started", {"resource": "host-1"}, state_bearing=True, agent_id="sec-agent"
    )
    assert seq == 1
    close_result = facade.sessions.close_session(sid)
    assert close_result.events_appended == 0
    assert close_result.session.status.value == "completed"

    events = facade.sessions.replay(sid)
    assert len(events) == 1
    assert events[0].kind == EventKind.CYCLE_STARTED
    assert events[0].state_bearing is True

    emitted = facade.sessions.emit(
        sid,
        EventKind.CYCLE_COMPLETED,
        {"done": True},
        state_bearing=True,
        metadata=EmitMetadata(agent_id="sec-agent"),
    )
    assert emitted == 2

    sid2 = facade.sessions.open_session("abort-demo", max_cost=Decimal("5"), max_action_count=1)
    abort_result = facade.sessions.abort_session(sid2, reason="operator stop")
    assert abort_result.session.status.value == "aborted"
    facade.close()


def test_control_plane_facade_command_id_idempotency(tmp_path: Path):
    db_file = tmp_path / "cp_facade_idempotency.db"
    facade = ControlPlaneFacade.from_database_url(f"sqlite:///{db_file}")
    facade.setup()

    sid = facade.sessions.open_session("idempotency-demo", command_id="sync-open-1")
    sid_again = facade.sessions.open_session("ignored-name", command_id="sync-open-1")
    assert sid_again == sid

    seq1 = facade.sessions.emit(
        sid, EventKind.CYCLE_STARTED, {"phase": "one"}, metadata=EmitMetadata(command_id="sync-emit-1")
    )
    seq2 = facade.sessions.emit(
        sid, EventKind.CYCLE_STARTED, {"phase": "two"}, metadata=EmitMetadata(command_id="sync-emit-1")
    )
    assert seq2 == seq1

    close1 = facade.sessions.close_session(sid, command_id="sync-close-1")
    close2 = facade.sessions.close_session(sid, command_id="sync-close-1")
    assert close1.session.status.value == "completed"
    assert close2.session.status.value == "completed"

    sid_kill = facade.sessions.open_session("kill-target")
    kill1 = facade.sessions.kill_session(sid_kill, command_id="sync-kill-1")
    kill2 = facade.sessions.kill_session(sid_kill, command_id="sync-kill-1")
    assert kill1.scope == kill2.scope
    assert kill1.session_id == kill2.session_id
    assert kill1.tickets_denied == kill2.tickets_denied

    facade.close()


def test_control_plane_facade_approval_flows_and_idempotency(tmp_path: Path):
    db_file = tmp_path / "cp_facade_approvals.db"
    facade = ControlPlaneFacade.from_database_url(f"sqlite:///{db_file}")
    facade.setup()

    sid = facade.sessions.open_session("sync-approvals")
    proposal_id = _insert_pending_proposal(facade, sid, resource_id="sync-asset-1")

    timeout_at = datetime.now(UTC) + timedelta(minutes=10)
    ticket = facade.approvals.create_ticket(sid, proposal_id, timeout_at, command_id="sync-ticket-create-1")
    ticket_again = facade.approvals.create_ticket(sid, proposal_id, timeout_at, command_id="sync-ticket-create-1")
    assert ticket_again.id == ticket.id
    assert ticket_again.status == ApprovalStatus.PENDING

    approved = facade.approvals.approve_ticket(
        ticket.id,
        reason="sync approve",
        command_id="sync-ticket-approve-1",
    )
    approved_again = facade.approvals.approve_ticket(
        ticket.id,
        reason="ignored",
        command_id="sync-ticket-approve-1",
    )
    assert approved.status == ApprovalStatus.APPROVED
    assert approved_again.status == ApprovalStatus.APPROVED

    approved_proposal = facade.approvals.get_proposal(proposal_id)
    assert approved_proposal is not None
    assert approved_proposal.status == ProposalStatus.APPROVED

    proposal_id_2 = _insert_pending_proposal(facade, sid, resource_id="sync-asset-2")
    ticket_2 = facade.approvals.create_ticket(sid, proposal_id_2, datetime.now(UTC) + timedelta(minutes=5))
    denied = facade.approvals.deny_ticket(ticket_2.id, reason="sync deny", command_id="sync-ticket-deny-1")
    denied_again = facade.approvals.deny_ticket(ticket_2.id, reason="ignored", command_id="sync-ticket-deny-1")
    assert denied.status == ApprovalStatus.DENIED
    assert denied_again.status == ApprovalStatus.DENIED

    denied_proposal = facade.approvals.get_proposal(proposal_id_2)
    assert denied_proposal is not None
    assert denied_proposal.status == ProposalStatus.DENIED

    facade.close()


def test_control_plane_facade_create_proposal_idempotency(tmp_path: Path):
    db_file = tmp_path / "cp_facade_create_proposal.db"
    facade = ControlPlaneFacade.from_database_url(f"sqlite:///{db_file}")
    facade.setup()

    sid = facade.sessions.open_session("sync-create-proposal")
    proposal = ActionProposal(
        session_id=sid,
        resource_id="sync-resource-1",
        resource_type="task",
        decision="status",
        reasoning="create proposal test",
        weight=Decimal("1.0"),
        score=Decimal("0.8"),
    )

    created = facade.approvals.create_proposal(proposal, command_id="sync-create-proposal-1")
    replayed = facade.approvals.create_proposal(proposal, command_id="sync-create-proposal-1")
    assert replayed.id == created.id

    second = facade.approvals.create_proposal(
        proposal.model_copy(update={"id": uuid4(), "resource_id": "sync-resource-2"}),
        command_id="sync-create-proposal-2",
    )
    assert second.id != created.id

    loaded = facade.approvals.get_proposal(created.id)
    assert loaded is not None
    assert loaded.id == created.id
    assert loaded.resource_id == "sync-resource-1"

    with pytest.raises(ValueError, match="already used for operation"):
        facade.approvals.create_ticket(
            sid,
            created.id,
            datetime.now(UTC) + timedelta(minutes=5),
            command_id="sync-create-proposal-1",
        )

    facade.close()


def test_control_plane_facade_verify_preconditions_records_failure(tmp_path: Path):
    db_file = tmp_path / "cp_facade_preconditions.db"
    facade = ControlPlaneFacade.from_database_url(f"sqlite:///{db_file}")
    facade.setup()

    sid = facade.sessions.open_session("sync-preconditions")
    result = facade.verify_preconditions(
        sid,
        [Precondition(resource_id="resource-1", provider_id="unknown-provider", expected_state="expected")],
        action_id="host-execution",
    )

    assert result.status == PreconditionStatus.FAILED
    events = facade.sessions.replay(sid)
    assert len(events) == 1
    assert events[0].kind == EventKind.PRECONDITION_FAILED
    assert events[0].state_bearing is True
    assert events[0].payload["action_id"] == "host-execution"

    facade.close()


def test_control_plane_facade_state_feed_projection_end_to_end(tmp_path: Path):
    db_file = tmp_path / "cp_sync_projection_e2e.db"
    facade = ControlPlaneFacade.from_database_url(f"sqlite:///{db_file}")
    facade.setup()

    sid = facade.sessions.open_session("sync-projection")
    proposal_id = _insert_pending_proposal(facade, sid, resource_id="projection-sync-asset-1")
    ticket = facade.approvals.create_ticket(sid, proposal_id, datetime.now(UTC) + timedelta(minutes=10))

    facade.sessions.emit(sid, EventKind.CYCLE_STARTED, {"phase": "start"}, state_bearing=True)
    facade.approvals.approve_ticket(ticket.id, reason="projection approve")
    facade.sessions.emit(sid, EventKind.CYCLE_COMPLETED, {"phase": "done"}, state_bearing=True)

    projection_tickets: dict[UUID, ApprovalStatus] = {}
    projection_proposals: dict[UUID, ProposalStatus] = {}
    cursor = 0

    while True:
        feed = facade.observer.get_state_change_feed(cursor=cursor, limit=10)
        if not feed.items:
            break
        for item in feed.items:
            session_id = item.event.session_id
            tickets_page = facade.approvals.list_tickets(session_id=session_id, limit=200, offset=0)
            for projected_ticket in tickets_page.items:
                projection_tickets[projected_ticket.id] = projected_ticket.status

            proposals_page = facade.approvals.list_proposals(session_id=session_id, limit=200, offset=0)
            for projected_proposal in proposals_page.items:
                projection_proposals[projected_proposal.id] = projected_proposal.status

            cursor = item.cursor

    canonical_ticket = facade.approvals.get_ticket(ticket.id)
    canonical_proposal = facade.approvals.get_proposal(proposal_id)

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


def test_sync_session_started_at_set_on_activation(tmp_path: Path):
    db_file = tmp_path / "started_at_sync.db"
    facade = ControlPlaneFacade.from_database_url(f"sqlite:///{db_file}")
    facade.setup()

    sid = facade.sessions.open_session("started-at-sync")

    session = facade.sessions.get_session(sid)
    assert session is not None
    assert session.started_at is None

    t_before = datetime.now(UTC).replace(tzinfo=None)
    facade._cp.activate_session(sid)
    t_after = datetime.now(UTC).replace(tzinfo=None)

    session = facade.sessions.get_session(sid)
    assert session is not None
    assert session.started_at is not None
    started = session.started_at.replace(tzinfo=None) if session.started_at.tzinfo else session.started_at
    assert t_before <= started <= t_after

    facade.close()


def test_sync_started_at_returned_in_lifecycle_result(tmp_path: Path):
    db_file = tmp_path / "started_at_result_sync.db"
    facade = ControlPlaneFacade.from_database_url(f"sqlite:///{db_file}")
    facade.setup()

    sid = facade.sessions.open_session("started-at-result-sync")
    result = facade._cp.activate_session(sid)

    assert result.session.started_at is not None

    facade.close()


def test_sync_list_sessions_includes_started_at(tmp_path: Path):
    db_file = tmp_path / "list_started_at_sync.db"
    facade = ControlPlaneFacade.from_database_url(f"sqlite:///{db_file}")
    facade.setup()

    sid = facade.sessions.open_session("list-started-at-sync")
    facade._cp.activate_session(sid)

    sessions = facade.observer.list_sessions(statuses=[SessionStatus.ACTIVE])
    target = next(s for s in sessions if s.id == sid)
    assert target.started_at is not None

    facade.close()


def test_control_plane_facade_run_failing_precondition_aborts_before_execution(tmp_path: Path):
    db_file = tmp_path / "run_precondition_fail.db"
    facade = ControlPlaneFacade.from_database_url(f"sqlite:///{db_file}")
    facade.setup()

    target = tmp_path / "config.py"
    target.write_text("SCALAR_LR = 0.5\n")
    expected_hash = hashlib.sha256(target.read_bytes()).hexdigest()
    target.write_text("SCALAR_LR = 0.3\n")

    executor_called = False

    with (
        pytest.raises(RuntimeError, match="precondition_failed"),
        facade.run(
            "precondition-fail-run",
            preconditions=[
                Precondition(
                    resource_id=str(target),
                    provider_id="file_sha256",
                    expected_state=expected_hash,
                )
            ],
        ) as _handle,
    ):
        executor_called = True

    assert executor_called is False

    sessions = facade.observer.list_sessions(statuses=[SessionStatus.COMPLETED])
    assert len(sessions) == 1
    sid = sessions[0].id
    events = facade.sessions.replay(sid)
    failures = [e for e in events if e.kind == EventKind.PRECONDITION_FAILED]
    assert len(failures) == 1
    assert failures[0].state_bearing is True
    assert EventKind.EXECUTION_COMPLETED not in [e.kind for e in events]

    facade.close()


# ---------------------------------------------------------------------------
# SessionRiskAccumulator integration
# ---------------------------------------------------------------------------

_RISK_ACTIONS = ["read_crm", "query_db", "send_email"]


def _risk_policy() -> PolicySnapshot:
    return PolicySnapshot(
        action_tiers={
            "blocked": [],
            "always_approve": [],
            "auto_approve": _RISK_ACTIONS,
            "unrestricted": [],
        },
        risk_limits={"max_risk_score": "10000", "max_weight_pct": "5.0", "custom": {}},
        execution_mode=ExecutionMode.DRY_RUN,
        approval_timeout_seconds=300,
        auto_approve_conditions={
            "max_risk_tier": RiskLevel.HIGH,
            "dry_run_only": True,
            "max_weight": "2.5",
            "min_score": "0.7",
        },
    )


def _risk_proposal(session_id: UUID, decision: str) -> ActionProposal:
    return ActionProposal(
        session_id=session_id,
        resource_id="res-1",
        resource_type="task",
        decision=decision,
        reasoning="risk integration test",
        weight=Decimal("1.0"),
        score=Decimal("0.9"),
    )


def test_route_proposal_risk_escalation_emits_event_and_upgrades_tier(tmp_path: Path):
    """Two proposals through the same session — second triggers score-based escalation."""
    from agent_control_plane.types.enums import register_action_names

    register_action_names(_RISK_ACTIONS)

    pattern = RiskPattern(
        name="exfil_chain",
        description="CRM read → DB query = elevated exfil risk",
        action_sequence=["read_crm", "query_db"],
        window_size=5,
        escalate_to=RiskLevel.HIGH,
    )
    accumulator = SessionRiskAccumulator(patterns=[pattern])

    db_file = tmp_path / "risk_integration.db"
    facade = ControlPlaneFacade.from_database_url(
        f"sqlite:///{db_file}",
        risk_accumulator=accumulator,
    )
    facade.setup()
    policy = _risk_policy()

    session_id = facade.sessions.open_session("risk-test", execution_mode=ExecutionMode.DRY_RUN)

    # First proposal — no prior history; risk stays LOW
    p1 = _risk_proposal(session_id, "read_crm")
    decision1 = facade.route_proposal(p1, policy)
    assert decision1.risk_escalated is False
    assert decision1.risk_level == RiskLevel.LOW

    # Second proposal — pattern [read_crm, query_db] now complete; HIGH escalation expected
    p2 = _risk_proposal(session_id, "query_db")
    decision2 = facade.route_proposal(p2, policy)
    assert decision2.risk_escalated is True
    assert decision2.risk_level == RiskLevel.HIGH

    # SESSION_RISK_ESCALATED event must be in the audit log
    events = facade.sessions.replay(session_id)
    escalation_events = [e for e in events if e.kind == EventKind.SESSION_RISK_ESCALATED]
    assert len(escalation_events) == 1
    payload = escalation_events[0].payload
    assert payload["escalated_risk"] == RiskLevel.HIGH.value
    assert payload["original_risk"] == RiskLevel.LOW.value

    facade.close()


# ---------------------------------------------------------------------------
# revoke_ticket terminal-state guard
# ---------------------------------------------------------------------------


def _approve_ticket(facade: ControlPlaneFacade, session_id: UUID, resource_id: str):
    """Helper: insert proposal, create ticket, approve it. Returns (proposal_id, ticket)."""
    proposal_id = _insert_pending_proposal(facade, session_id, resource_id=resource_id)
    ticket = facade.approvals.create_ticket(session_id, proposal_id, datetime.now(UTC) + timedelta(minutes=10))
    approved = facade.approvals.approve_ticket(ticket.id)
    return proposal_id, approved


def _set_proposal_status(facade: ControlPlaneFacade, proposal_id: UUID, status: ProposalStatus) -> None:
    with facade._cp.session_scope() as db:
        proposal_model = ModelRegistry.get("ActionProposal")
        from sqlalchemy import update as sa_update

        db.execute(sa_update(proposal_model).where(proposal_model.id == proposal_id).values(status=status))
        db.commit()


@pytest.mark.parametrize("terminal_status", [ProposalStatus.EXECUTED, ProposalStatus.FAILED])
def test_revoke_ticket_raises_when_proposal_already_terminal(tmp_path: Path, terminal_status: ProposalStatus):
    facade = ControlPlaneFacade.from_database_url(f"sqlite:///{tmp_path / 'revoke_terminal.db'}")
    facade.setup()

    sid = facade.sessions.open_session("revoke-terminal-test")
    proposal_id, ticket = _approve_ticket(facade, sid, resource_id="res-terminal")
    _set_proposal_status(facade, proposal_id, terminal_status)

    with pytest.raises(ValueError, match="already executed"):
        facade.approvals.revoke_ticket(ticket.id)

    # Ticket must remain APPROVED — no partial mutation
    unchanged = facade.approvals.get_ticket(ticket.id)
    assert unchanged is not None
    assert unchanged.status == ApprovalStatus.APPROVED

    facade.close()


def test_revoke_ticket_succeeds_when_proposal_approved(tmp_path: Path):
    facade = ControlPlaneFacade.from_database_url(f"sqlite:///{tmp_path / 'revoke_ok.db'}")
    facade.setup()

    sid = facade.sessions.open_session("revoke-ok-test")
    proposal_id, ticket = _approve_ticket(facade, sid, resource_id="res-ok")

    revoked = facade.approvals.revoke_ticket(ticket.id, revoked_by="operator", reason="stale")
    assert revoked.status == ApprovalStatus.REVOKED
    assert revoked.revoked_by == "operator"

    proposal = facade.approvals.get_proposal(proposal_id)
    assert proposal is not None
    assert proposal.status == ProposalStatus.PENDING

    facade.close()
