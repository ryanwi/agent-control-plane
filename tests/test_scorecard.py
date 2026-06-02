"""Unit tests for pure scorecard accumulation (_scorecard.py)."""

from __future__ import annotations

from uuid import uuid4

from agent_control_plane._scorecard import ScorecardAcc, accumulate_scorecard_event
from agent_control_plane.types.agentic import ControlPlaneScorecard
from agent_control_plane.types.enums import EventKind
from agent_control_plane.types.frames import EventFrame


def _event(kind: EventKind, seq: int) -> EventFrame:
    return EventFrame(session_id=uuid4(), seq=seq, kind=kind, payload={})


def _accumulate(kinds: list[EventKind]) -> ControlPlaneScorecard:
    sc = ControlPlaneScorecard()
    acc = ScorecardAcc()
    for i, kind in enumerate(kinds, start=1):
        accumulate_scorecard_event(_event(kind, i), sc, acc, None, None)
    return sc


def test_approval_grant_and_deny_are_counted_separately():
    sc = _accumulate(
        [
            EventKind.APPROVAL_GRANTED,
            EventKind.APPROVAL_GRANTED,
            EventKind.APPROVAL_GRANTED,
            EventKind.APPROVAL_DENIED,
        ]
    )
    assert sc.approvals_granted == 3
    assert sc.approvals_denied == 1


def test_no_approvals_leaves_counts_zero():
    sc = _accumulate([EventKind.EXECUTION_COMPLETED])
    assert sc.approvals_granted == 0
    assert sc.approvals_denied == 0
