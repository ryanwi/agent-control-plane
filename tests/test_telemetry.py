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


def test_export_event_maps_core_attributes() -> None:
    tracer = _Tracer()
    event = EventFrame(
        session_id=uuid4(),
        seq=3,
        event_kind=EventKind.GUARDRAIL_INPUT,
        payload={"policy_code": "CP-GR-1", "decision": "deny"},
        state_bearing=False,
        agent_id="agent-1",
    )

    export_event(event, tracer=tracer)

    assert len(tracer.events) == 1
    name, attrs = tracer.events[0]
    assert name == "agent_control_plane.event"
    assert attrs["event_kind"] == EventKind.GUARDRAIL_INPUT.value
    assert attrs["policy_code"] == "CP-GR-1"
    assert attrs["decision"] == "deny"
    assert attrs["agent_id"] == "agent-1"


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
