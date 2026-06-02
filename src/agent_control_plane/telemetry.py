"""OpenTelemetry-friendly export helpers for control-plane events and scorecards."""

from __future__ import annotations

from typing import Any, Protocol

from agent_control_plane.types.agentic import ControlPlaneScorecard
from agent_control_plane.types.enums import EventKind, GovernanceOutcome
from agent_control_plane.types.frames import EventFrame


class TracerLike(Protocol):
    def add_event(self, name: str, attributes: dict[str, Any]) -> None: ...


class MeterLike(Protocol):
    def record(self, name: str, value: float, attributes: dict[str, Any]) -> None: ...


_OUTCOME_MAP: dict[EventKind, GovernanceOutcome] = {
    EventKind.APPROVAL_GRANTED: GovernanceOutcome.ACCEPTED,
    EventKind.EXECUTION_COMPLETED: GovernanceOutcome.APPLIED,
    EventKind.PLAN_STEP_COMPLETED: GovernanceOutcome.APPLIED,
    EventKind.APPROVAL_DENIED: GovernanceOutcome.DENIED,
    EventKind.EVALUATION_BLOCKED: GovernanceOutcome.DENIED,
    EventKind.GUARDRAIL_TOOL: GovernanceOutcome.DENIED,
    EventKind.GUARDRAIL_OUTPUT: GovernanceOutcome.DENIED,
    EventKind.APPROVAL_TIMEOUT: GovernanceOutcome.TIMEOUT,
    EventKind.LEASE_EXPIRED: GovernanceOutcome.STALE_TARGET,
}

_HANDOFF_REJECTED_FLAGS: list[tuple[str, GovernanceOutcome]] = [
    ("stale_target", GovernanceOutcome.STALE_TARGET),
    ("wrong_session", GovernanceOutcome.WRONG_SESSION),
    ("no_live_target", GovernanceOutcome.NO_LIVE_TARGET),
]

# MCP app-event kinds that map to standard outcomes
_MCP_OUTCOME_MAP: dict[str, GovernanceOutcome] = {
    "tool_call_executed": GovernanceOutcome.APPLIED,
    "tool_call_blocked": GovernanceOutcome.DENIED,
    "tool_result_rejected": GovernanceOutcome.DENIED,
}


def _compute_outcome(kind: EventKind, payload: dict[str, Any]) -> GovernanceOutcome | None:
    # Payload override takes precedence
    raw = payload.get("outcome")
    if raw:
        try:
            return GovernanceOutcome(raw)
        except ValueError:
            pass

    if kind == EventKind.HANDOFF_REJECTED:
        for flag, outcome in _HANDOFF_REJECTED_FLAGS:
            if payload.get(flag):
                return outcome
        return None

    mapped = _OUTCOME_MAP.get(kind)
    if mapped is not None:
        return mapped

    # MCP app-event kind values surfaced via payload["mcp_event"] or event_kind string
    mcp_event = payload.get("mcp_event") or payload.get("event_name")
    if isinstance(mcp_event, str):
        return _MCP_OUTCOME_MAP.get(mcp_event)

    return None


_PAYLOAD_ATTR_KEYS: list[tuple[str, str]] = [
    ("cp.action_id", "action_id"),
    ("cp.action_id", "proposal_id"),
    ("cp.policy_snapshot_id", "policy_snapshot_id"),
    ("cp.policy_snapshot_id", "policy_id"),
    ("cp.runtime_kind", "runtime_kind"),
    ("cp.live_target_id", "live_target_id"),
    ("cp.cwd", "cwd"),
    ("cp.worktree", "worktree"),
    ("cp.project_id", "project_id"),
]


def export_event(event: EventFrame, *, tracer: TracerLike) -> None:
    attrs: dict[str, Any] = {
        "cp.session_id": str(event.session_id),
        "cp.event_id": str(event.event_id),
        "cp.event_kind": event.kind.value,
        "cp.seq": event.seq,
        "cp.state_bearing": event.state_bearing,
    }
    if event.agent_id is not None:
        attrs["cp.agent_id"] = str(event.agent_id)
    if event.correlation_id is not None:
        attrs["cp.correlation_id"] = str(event.correlation_id)

    payload = event.payload if isinstance(event.payload, dict) else {}

    # Preserve existing payload extractions under legacy keys
    if "policy_code" in payload:
        attrs["policy_code"] = payload["policy_code"]
    if "decision" in payload:
        attrs["decision"] = payload["decision"]

    # Enriched payload attributes (first matching key wins per attr)
    seen_attrs: set[str] = set()
    for attr_name, payload_key in _PAYLOAD_ATTR_KEYS:
        if attr_name not in seen_attrs and payload_key in payload:
            attrs[attr_name] = str(payload[payload_key])
            seen_attrs.add(attr_name)

    outcome = _compute_outcome(event.kind, payload)
    if outcome is not None:
        attrs["cp.outcome"] = outcome.value

    tracer.add_event("agent_control_plane.governance", attrs)


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
    meter.record("cp.approvals_granted", float(scorecard.approvals_granted), base_attrs)
    meter.record("cp.approvals_denied", float(scorecard.approvals_denied), base_attrs)
    # Approval-fatigue signal: a grant rate near 1.0 across many approvals suggests rubber-stamping.
    if scorecard.approval_grant_rate is not None:
        meter.record("cp.approval_grant_rate", scorecard.approval_grant_rate, base_attrs)
