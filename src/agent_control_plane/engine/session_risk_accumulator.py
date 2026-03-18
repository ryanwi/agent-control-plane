"""Session-level risk accumulation across action chains."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from agent_control_plane.types.enums import EventKind, RiskLevel
from agent_control_plane.types.risk import RiskPattern, SessionRiskEscalation, SessionRiskState

if TYPE_CHECKING:
    from agent_control_plane.engine.event_store import EventStore
    from agent_control_plane.types.proposals import ActionProposal

logger = logging.getLogger(__name__)

_SCORE_WEIGHTS: dict[RiskLevel, Decimal] = {
    RiskLevel.LOW: Decimal("1.0"),
    RiskLevel.MEDIUM: Decimal("3.0"),
    RiskLevel.HIGH: Decimal("5.0"),
}


class SessionRiskAccumulator:
    """Watches action chains across a session and escalates risk when patterns
    or accumulated score thresholds are detected.

    Slots between classify_risk_level() and classify_action_tier() in the
    policy flow. Host apps call it as an optional step; it is not wired into
    ProposalRouter or SyncControlPlane.
    """

    def __init__(
        self,
        patterns: list[RiskPattern] | None = None,
        score_threshold_medium: Decimal = Decimal("5.0"),
        score_threshold_high: Decimal = Decimal("10.0"),
        event_store: EventStore | None = None,
    ) -> None:
        self._patterns = patterns or []
        self._score_threshold_medium = score_threshold_medium
        self._score_threshold_high = score_threshold_high
        self._event_store = event_store
        self._states: dict[UUID, SessionRiskState] = {}
        self._max_window: int = max((p.window_size for p in self._patterns), default=0)

    async def assess(
        self,
        session_id: UUID,
        proposal: ActionProposal,
        action_risk_level: RiskLevel,
    ) -> SessionRiskEscalation:
        """Assess a single action against accumulated session risk.

        Updates internal state and returns an escalation result.
        Score is added before threshold check, so escalation applies to the
        current action.
        """
        state = self._get_or_create_state(session_id)

        # Record this action
        action_name = str(proposal.decision)
        new_recent = [*state.recent_actions, action_name]
        if self._max_window > 0:
            new_recent = new_recent[-self._max_window :]

        new_score = state.accumulated_score + self._score_contribution(action_risk_level)
        new_count = state.action_count + 1

        # Check score-based escalation
        score_risk, score_reasons = self._check_score_escalation(new_score, action_risk_level)

        # Check pattern-based escalation against updated window
        pattern_risk, pattern_reasons = self._check_pattern_escalation(new_recent)

        # Determine final escalated risk (max of all sources)
        candidates = [action_risk_level, score_risk]
        if pattern_risk is not None:
            candidates.append(pattern_risk)
        escalated_risk = max(candidates, key=lambda r: r.rank)

        all_reasons = score_reasons + pattern_reasons
        was_escalated = escalated_risk.rank > action_risk_level.rank

        # Update detected_patterns list
        new_detected = list(state.detected_patterns)
        for p in self._patterns:
            window = new_recent[-p.window_size :] if p.window_size > 0 else new_recent
            if _is_contiguous_subsequence(p.action_sequence, window) and p.name not in new_detected:
                new_detected.append(p.name)

        new_state = SessionRiskState(
            session_id=session_id,
            accumulated_score=new_score,
            action_count=new_count,
            recent_actions=new_recent,
            detected_patterns=new_detected,
            current_risk_level=escalated_risk,
        )
        self._states[session_id] = new_state

        if was_escalated and self._event_store is not None:
            await self._event_store.append(
                session_id,
                EventKind.SESSION_RISK_ESCALATED,
                {
                    "session_id": str(session_id),
                    "original_risk": action_risk_level.value,
                    "escalated_risk": escalated_risk.value,
                    "reasons": all_reasons,
                },
                state_bearing=False,
            )

        return SessionRiskEscalation(
            original_risk=action_risk_level,
            escalated_risk=escalated_risk,
            escalation_reasons=all_reasons,
            session_state=new_state,
            was_escalated=was_escalated,
        )

    def get_state(self, session_id: UUID) -> SessionRiskState | None:
        """Return the current accumulated state for a session, or None if unknown."""
        return self._states.get(session_id)

    def reset_session(self, session_id: UUID) -> None:
        """Clear accumulated state for a session. No-op if session is unknown."""
        self._states.pop(session_id, None)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _score_contribution(self, risk_level: RiskLevel) -> Decimal:
        return _SCORE_WEIGHTS[risk_level]

    def _check_score_escalation(
        self, accumulated: Decimal, current_action_risk: RiskLevel
    ) -> tuple[RiskLevel, list[str]]:
        """Return the risk level implied by the accumulated score and any reasons."""
        if accumulated >= self._score_threshold_high:
            level = RiskLevel.HIGH
            if level.rank > current_action_risk.rank:
                return level, [f"Accumulated score {accumulated} >= high threshold {self._score_threshold_high}"]
        elif accumulated >= self._score_threshold_medium:
            level = RiskLevel.MEDIUM
            if level.rank > current_action_risk.rank:
                return level, [f"Accumulated score {accumulated} >= medium threshold {self._score_threshold_medium}"]
        return current_action_risk, []

    def _check_pattern_escalation(self, recent_actions: list[str]) -> tuple[RiskLevel | None, list[str]]:
        """Return the highest escalation level from any matched patterns, and reasons."""
        matched_level: RiskLevel | None = None
        reasons: list[str] = []
        for p in self._patterns:
            window = recent_actions[-p.window_size :] if p.window_size > 0 else recent_actions
            if _is_contiguous_subsequence(p.action_sequence, window):
                reasons.append(f"Pattern matched: {p.name}")
                if matched_level is None or p.escalate_to.rank > matched_level.rank:
                    matched_level = p.escalate_to
        return matched_level, reasons

    def _get_or_create_state(self, session_id: UUID) -> SessionRiskState:
        if session_id not in self._states:
            self._states[session_id] = SessionRiskState(
                session_id=session_id,
                accumulated_score=Decimal("0"),
                action_count=0,
                recent_actions=[],
                detected_patterns=[],
                current_risk_level=RiskLevel.LOW,
            )
        return self._states[session_id]


def _is_contiguous_subsequence(sequence: list[str], window: list[str]) -> bool:
    """Return True if sequence appears as a contiguous ordered subsequence in window."""
    if not sequence:
        return False
    n, m = len(window), len(sequence)
    if m > n:
        return False
    return any(window[i : i + m] == sequence for i in range(n - m + 1))
