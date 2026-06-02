from __future__ import annotations

from uuid import uuid4

from agent_control_plane.telemetry import export_event, export_scorecard
from agent_control_plane.types.agentic import ControlPlaneScorecard
from agent_control_plane.types.enums import EventKind
from agent_control_plane.types.frames import EventFrame


class _Tracer:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def add_event(self, name: str, attributes: dict[str, object]) -> None:
        self.events.append((name, attributes))


class _Meter:
    def __init__(self) -> None:
        self.records: list[tuple[str, float, dict[str, object]]] = []

    def record(self, name: str, value: float, attributes: dict[str, object]) -> None:
        self.records.append((name, value, attributes))


def _make_event(kind: EventKind, payload: dict | None = None, **kwargs) -> EventFrame:
    return EventFrame(
        session_id=uuid4(),
        seq=1,
        kind=kind,
        payload=payload or {},
        state_bearing=False,
        **kwargs,
    )


def _export(kind: EventKind, payload: dict | None = None, **kwargs) -> dict:
    tracer = _Tracer()
    export_event(_make_event(kind, payload, **kwargs), tracer=tracer)
    assert len(tracer.events) == 1
    name, attrs = tracer.events[0]
    assert name == "agent_control_plane.governance"
    return attrs


# --- core attributes ---


def test_export_event_maps_core_attributes() -> None:
    tracer = _Tracer()
    event = EventFrame(
        session_id=uuid4(),
        seq=3,
        kind=EventKind.GUARDRAIL_INPUT,
        payload={"policy_code": "CP-GR-1", "decision": "deny"},
        state_bearing=False,
        agent_id="agent-1",
    )

    export_event(event, tracer=tracer)

    assert len(tracer.events) == 1
    name, attrs = tracer.events[0]
    assert name == "agent_control_plane.governance"
    assert attrs["cp.event_kind"] == EventKind.GUARDRAIL_INPUT.value
    assert attrs["policy_code"] == "CP-GR-1"
    assert attrs["decision"] == "deny"
    assert attrs["cp.agent_id"] == "agent-1"
    assert attrs["cp.seq"] == 3
    assert attrs["cp.state_bearing"] is False


# --- outcome mapping ---


def test_governance_outcome_accepted_on_approval_granted() -> None:
    attrs = _export(EventKind.APPROVAL_GRANTED)
    assert attrs["cp.outcome"] == "accepted"


def test_governance_outcome_applied_on_execution_completed() -> None:
    attrs = _export(EventKind.EXECUTION_COMPLETED)
    assert attrs["cp.outcome"] == "applied"


def test_governance_outcome_applied_on_plan_step_completed() -> None:
    attrs = _export(EventKind.PLAN_STEP_COMPLETED)
    assert attrs["cp.outcome"] == "applied"


def test_governance_outcome_denied_on_approval_denied() -> None:
    attrs = _export(EventKind.APPROVAL_DENIED)
    assert attrs["cp.outcome"] == "denied"


def test_governance_outcome_denied_on_evaluation_blocked() -> None:
    attrs = _export(EventKind.EVALUATION_BLOCKED)
    assert attrs["cp.outcome"] == "denied"


def test_governance_outcome_timeout_on_approval_timeout() -> None:
    attrs = _export(EventKind.APPROVAL_TIMEOUT)
    assert attrs["cp.outcome"] == "timeout"


def test_governance_outcome_stale_target_on_lease_expired() -> None:
    attrs = _export(EventKind.LEASE_EXPIRED)
    assert attrs["cp.outcome"] == "stale-target"


def test_governance_outcome_handoff_stale_target_from_payload() -> None:
    attrs = _export(EventKind.HANDOFF_REJECTED, {"stale_target": True})
    assert attrs["cp.outcome"] == "stale-target"


def test_governance_outcome_handoff_wrong_session_from_payload() -> None:
    attrs = _export(EventKind.HANDOFF_REJECTED, {"wrong_session": True})
    assert attrs["cp.outcome"] == "wrong-session"


def test_governance_outcome_handoff_no_live_target_from_payload() -> None:
    attrs = _export(EventKind.HANDOFF_REJECTED, {"no_live_target": True})
    assert attrs["cp.outcome"] == "no-live-target"


def test_governance_outcome_none_for_lifecycle_events() -> None:
    attrs = _export(EventKind.CYCLE_STARTED)
    assert "cp.outcome" not in attrs


def test_governance_outcome_handoff_rejected_no_flag_omits_outcome() -> None:
    attrs = _export(EventKind.HANDOFF_REJECTED, {})
    assert "cp.outcome" not in attrs


def test_outcome_override_from_payload() -> None:
    attrs = _export(EventKind.CYCLE_STARTED, {"outcome": "applied"})
    assert attrs["cp.outcome"] == "applied"


def test_outcome_override_invalid_value_falls_through_to_map() -> None:
    attrs = _export(EventKind.APPROVAL_GRANTED, {"outcome": "not-a-real-outcome"})
    assert attrs["cp.outcome"] == "accepted"


# --- enriched payload attributes ---


def test_payload_attrs_extracted_action_id() -> None:
    attrs = _export(EventKind.CYCLE_STARTED, {"action_id": "act-123"})
    assert attrs["cp.action_id"] == "act-123"


def test_payload_attrs_extracted_proposal_id_as_action_id() -> None:
    attrs = _export(EventKind.CYCLE_STARTED, {"proposal_id": "prop-456"})
    assert attrs["cp.action_id"] == "prop-456"


def test_payload_attrs_action_id_wins_over_proposal_id() -> None:
    attrs = _export(EventKind.CYCLE_STARTED, {"action_id": "act-1", "proposal_id": "prop-2"})
    assert attrs["cp.action_id"] == "act-1"


def test_payload_attrs_extracted_policy_snapshot_id() -> None:
    attrs = _export(EventKind.CYCLE_STARTED, {"policy_snapshot_id": "ps-789"})
    assert attrs["cp.policy_snapshot_id"] == "ps-789"


def test_payload_attrs_extracted_runtime_kind_and_live_target() -> None:
    attrs = _export(
        EventKind.EXECUTION_COMPLETED,
        {"runtime_kind": "tmux", "live_target_id": "session-abc"},
    )
    assert attrs["cp.runtime_kind"] == "tmux"
    assert attrs["cp.live_target_id"] == "session-abc"


def test_payload_attrs_extracted_cwd_worktree_project() -> None:
    attrs = _export(
        EventKind.CYCLE_STARTED,
        {"cwd": "/home/user/project", "worktree": "feat-branch", "project_id": "proj-1"},
    )
    assert attrs["cp.cwd"] == "/home/user/project"
    assert attrs["cp.worktree"] == "feat-branch"
    assert attrs["cp.project_id"] == "proj-1"


def test_missing_payload_attrs_are_omitted() -> None:
    attrs = _export(EventKind.CYCLE_STARTED, {})
    for key in ("cp.action_id", "cp.policy_snapshot_id", "cp.runtime_kind", "cp.live_target_id"):
        assert key not in attrs


# --- scorecard ---


def test_export_scorecard_records_expected_metrics() -> None:
    meter = _Meter()
    scorecard = ControlPlaneScorecard(
        total_events=10,
        checkpoints_created=1,
        rollbacks_completed=2,
        evaluations_blocked=3,
        guardrail_denies=4,
        handoffs_accepted=5,
        handoffs_rejected=6,
        budget_denied_count=7,
        budget_exhausted_count=8,
    )

    export_scorecard(scorecard, meter=meter)

    names = [name for name, _, _ in meter.records]
    assert "cp.total_events" in names
    assert "cp.budget_denied" in names
    assert "cp.budget_exhausted" in names
    assert len(meter.records) == 9
