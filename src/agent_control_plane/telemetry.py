"""OpenTelemetry-friendly export helpers for control-plane events and scorecards."""

from __future__ import annotations

from typing import Any, Protocol

from agent_control_plane.types.agentic import ControlPlaneScorecard
from agent_control_plane.types.frames import EventFrame


class TracerLike(Protocol):
    def add_event(self, name: str, attributes: dict[str, Any]) -> None: ...


class MeterLike(Protocol):
    def record(self, name: str, value: float, attributes: dict[str, Any]) -> None: ...


def export_event(event: EventFrame, *, tracer: TracerLike) -> None:
    attrs: dict[str, Any] = {
        "session_id": str(event.session_id),
        "event_id": str(event.event_id),
        "event_kind": event.event_kind.value,
        "seq": event.seq,
        "state_bearing": event.state_bearing,
    }
    if event.agent_id is not None:
        attrs["agent_id"] = str(event.agent_id)
    if event.correlation_id is not None:
        attrs["correlation_id"] = str(event.correlation_id)
    if isinstance(event.payload, dict):
        if "policy_code" in event.payload:
            attrs["policy_code"] = event.payload["policy_code"]
        if "decision" in event.payload:
            attrs["decision"] = event.payload["decision"]
    tracer.add_event("agent_control_plane.event", attrs)


def export_scorecard(scorecard: ControlPlaneScorecard, *, meter: MeterLike) -> None:
    base_attrs: dict[str, Any] = {"source": "agent_control_plane"}
    meter.record("cp.total_events", float(scorecard.total_events), base_attrs)
    meter.record("cp.checkpoints_created", float(scorecard.checkpoints_created), base_attrs)
    meter.record("cp.rollbacks_completed", float(scorecard.rollbacks_completed), base_attrs)
    meter.record("cp.evaluations_blocked", float(scorecard.evaluations_blocked), base_attrs)
    meter.record("cp.guardrail_denies", float(scorecard.guardrail_denies), base_attrs)
    meter.record("cp.handoffs_accepted", float(scorecard.handoffs_accepted), base_attrs)
    meter.record("cp.handoffs_rejected", float(scorecard.handoffs_rejected), base_attrs)
    meter.record("cp.budget_denied", float(scorecard.budget_denied_count), base_attrs)
    meter.record("cp.budget_exhausted", float(scorecard.budget_exhausted_count), base_attrs)
