"""Shared scorecard accumulation logic — pure, no I/O.

Used by both ControlPlaneFacade (sync) and AsyncControlPlaneFacade (async)
to avoid verbatim duplication of the event-scanning math.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from agent_control_plane.types.agentic import ControlPlaneScorecard
from agent_control_plane.types.enums import EventKind
from agent_control_plane.types.frames import EventFrame


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, round((pct / 100.0) * (len(ordered) - 1))))
    return ordered[idx]


def normalize_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


@dataclass
class ScorecardAcc:
    """Mutable per-session state accumulated while scanning events for a scorecard."""

    pending_checkpoint_at: datetime | None = None
    approval_requested_at: datetime | None = None
    approval_latencies: list[float] = field(default_factory=list)
    rollback_latencies: list[float] = field(default_factory=list)
    total_cost: float = 0.0
    successful_actions: int = 0


def _sc_checkpoint(event_at: datetime, _event: EventFrame, sc: ControlPlaneScorecard, acc: ScorecardAcc) -> None:
    sc.checkpoints_created += 1
    acc.pending_checkpoint_at = event_at


def _sc_rollback(event_at: datetime, _event: EventFrame, sc: ControlPlaneScorecard, acc: ScorecardAcc) -> None:
    sc.rollbacks_completed += 1
    if acc.pending_checkpoint_at is not None:
        acc.rollback_latencies.append((event_at - acc.pending_checkpoint_at).total_seconds() * 1000.0)


def _sc_eval_blocked(_at: datetime, event: EventFrame, sc: ControlPlaneScorecard, _acc: ScorecardAcc) -> None:
    sc.evaluations_blocked += 1
    if isinstance(event.payload, dict):
        for reason in event.payload.get("reasons", []):
            key = str(reason)
            sc.evaluation_block_reasons[key] = sc.evaluation_block_reasons.get(key, 0) + 1


def _sc_guardrail(_at: datetime, event: EventFrame, sc: ControlPlaneScorecard, _acc: ScorecardAcc) -> None:
    if not isinstance(event.payload, dict):
        return
    code = str(event.payload.get("policy_code", "unknown"))
    sc.guardrail_policy_code_counts[code] = sc.guardrail_policy_code_counts.get(code, 0) + 1
    if event.payload.get("allow") is False:
        sc.guardrail_denies += 1
    else:
        sc.guardrail_allows += 1


def _sc_handoff_accepted(_at: datetime, _event: EventFrame, sc: ControlPlaneScorecard, _acc: ScorecardAcc) -> None:
    sc.handoffs_accepted += 1


def _sc_handoff_rejected(_at: datetime, _event: EventFrame, sc: ControlPlaneScorecard, _acc: ScorecardAcc) -> None:
    sc.handoffs_rejected += 1


def _sc_approval_requested(
    event_at: datetime, _event: EventFrame, _sc: ControlPlaneScorecard, acc: ScorecardAcc
) -> None:
    acc.approval_requested_at = event_at


def _sc_approval_resolved(
    event_at: datetime, _event: EventFrame, _sc: ControlPlaneScorecard, acc: ScorecardAcc
) -> None:
    if acc.approval_requested_at is not None:
        acc.approval_latencies.append((event_at - acc.approval_requested_at).total_seconds() * 1000.0)
        acc.approval_requested_at = None


def _sc_budget_exhausted(_at: datetime, _event: EventFrame, sc: ControlPlaneScorecard, _acc: ScorecardAcc) -> None:
    sc.budget_exhausted_count += 1


def _sc_execution_completed(_at: datetime, event: EventFrame, _sc: ControlPlaneScorecard, acc: ScorecardAcc) -> None:
    acc.successful_actions += 1
    if isinstance(event.payload, dict):
        value = event.payload.get("cost")
        if isinstance(value, int | float):
            acc.total_cost += float(value)


ScorecardHandler = Callable[[datetime, EventFrame, ControlPlaneScorecard, ScorecardAcc], None]

SCORECARD_HANDLERS: dict[EventKind, ScorecardHandler] = {
    EventKind.CHECKPOINT_CREATED: _sc_checkpoint,
    EventKind.ROLLBACK_COMPLETED: _sc_rollback,
    EventKind.EVALUATION_BLOCKED: _sc_eval_blocked,
    EventKind.GUARDRAIL_INPUT: _sc_guardrail,
    EventKind.GUARDRAIL_TOOL: _sc_guardrail,
    EventKind.GUARDRAIL_OUTPUT: _sc_guardrail,
    EventKind.HANDOFF_ACCEPTED: _sc_handoff_accepted,
    EventKind.HANDOFF_REJECTED: _sc_handoff_rejected,
    EventKind.APPROVAL_REQUESTED: _sc_approval_requested,
    EventKind.APPROVAL_GRANTED: _sc_approval_resolved,
    EventKind.APPROVAL_DENIED: _sc_approval_resolved,
    EventKind.BUDGET_EXHAUSTED: _sc_budget_exhausted,
    EventKind.EXECUTION_COMPLETED: _sc_execution_completed,
}


def accumulate_scorecard_event(
    event: EventFrame,
    sc: ControlPlaneScorecard,
    acc: ScorecardAcc,
    window_start: datetime | None,
    window_end: datetime | None,
) -> None:
    event_at = normalize_utc(event.created_at)
    if window_start and event_at < window_start:
        return
    if window_end and event_at > window_end:
        return
    sc.total_events += 1
    handler = SCORECARD_HANDLERS.get(event.kind)
    if handler:
        handler(event_at, event, sc, acc)
