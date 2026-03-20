"""Tests for SessionRiskAccumulator — TDD suite."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from agent_control_plane.engine.event_store import EventStore
from agent_control_plane.engine.session_risk_accumulator import SessionRiskAccumulator
from agent_control_plane.types.enums import EventKind, RiskLevel, register_action_names
from agent_control_plane.types.proposals import ActionProposal
from agent_control_plane.types.risk import RiskPattern

from .fakes import InMemoryEventRepository

# Register custom action names used in tests so parse_action_name passes them through
_CUSTOM_ACTIONS = ["read_crm", "query_database", "send_email", "other_a", "other_b", "other_c"]
register_action_names(_CUSTOM_ACTIONS)


@pytest.fixture(autouse=True)
def _clear_registered_names():
    """Re-register custom names before each test and ensure cleanup after."""
    register_action_names(_CUSTOM_ACTIONS)
    yield
    # Keep registered (module-level registration is idempotent); clear only if needed
    # We leave names registered so module-level imports work consistently


# ---------------------------------------------------------------------------
# Inline construction helpers
# ---------------------------------------------------------------------------


def _proposal(session_id: UUID, decision: str = "read_crm") -> ActionProposal:
    return ActionProposal(
        session_id=session_id,
        resource_id="res-1",
        resource_type="task",
        decision=decision,
        reasoning="test",
    )


def _accumulator(**overrides) -> SessionRiskAccumulator:
    return SessionRiskAccumulator(**overrides)


def _exfil_pattern() -> RiskPattern:
    return RiskPattern(
        name="data_exfiltration",
        description="CRM read → DB query → email = data exfiltration chain",
        action_sequence=["read_crm", "query_database", "send_email"],
        window_size=10,
        escalate_to=RiskLevel.HIGH,
    )


# ---------------------------------------------------------------------------
# TestScoreAccumulation
# ---------------------------------------------------------------------------


class TestScoreAccumulation:
    @pytest.mark.asyncio
    async def test_single_low_no_escalation(self):
        acc = _accumulator()
        sid = uuid4()
        result = await acc.assess(sid, _proposal(sid), RiskLevel.LOW)
        assert result.escalated_risk == RiskLevel.LOW
        assert result.was_escalated is False

    @pytest.mark.asyncio
    async def test_multi_low_accumulates(self):
        acc = _accumulator()
        sid = uuid4()
        # 4 LOW actions = score 4.0 (below medium threshold of 5.0)
        for _ in range(4):
            result = await acc.assess(sid, _proposal(sid), RiskLevel.LOW)
        assert result.session_state.accumulated_score == Decimal("4.0")
        assert result.was_escalated is False

    @pytest.mark.asyncio
    async def test_high_action_bumps_score(self):
        acc = _accumulator()
        sid = uuid4()
        result = await acc.assess(sid, _proposal(sid), RiskLevel.HIGH)
        assert result.session_state.accumulated_score == Decimal("5.0")

    @pytest.mark.asyncio
    async def test_crosses_medium_threshold(self):
        # 5 LOW actions = score 5.0, crosses medium threshold on action 5
        acc = _accumulator()
        sid = uuid4()
        for _ in range(4):
            await acc.assess(sid, _proposal(sid), RiskLevel.LOW)
        result = await acc.assess(sid, _proposal(sid), RiskLevel.LOW)
        assert result.escalated_risk == RiskLevel.MEDIUM
        assert result.was_escalated is True

    @pytest.mark.asyncio
    async def test_crosses_high_threshold(self):
        # 10 LOW actions = score 10.0, crosses high threshold
        acc = _accumulator()
        sid = uuid4()
        for _ in range(9):
            await acc.assess(sid, _proposal(sid), RiskLevel.LOW)
        result = await acc.assess(sid, _proposal(sid), RiskLevel.LOW)
        assert result.escalated_risk == RiskLevel.HIGH
        assert result.was_escalated is True

    @pytest.mark.asyncio
    async def test_no_double_escalation_when_already_high(self):
        # A HIGH action at score >= threshold should not claim was_escalated if
        # the action is already at or above the threshold level
        acc = _accumulator()
        sid = uuid4()
        # 2 HIGH actions → score 10.0 → crosses high threshold, but action is already HIGH
        await acc.assess(sid, _proposal(sid), RiskLevel.HIGH)
        result = await acc.assess(sid, _proposal(sid), RiskLevel.HIGH)
        # score is 10.0 which hits HIGH threshold, but action_risk is already HIGH
        assert result.escalated_risk == RiskLevel.HIGH
        assert result.was_escalated is False

    @pytest.mark.asyncio
    async def test_score_in_state(self):
        acc = _accumulator()
        sid = uuid4()
        await acc.assess(sid, _proposal(sid), RiskLevel.LOW)
        await acc.assess(sid, _proposal(sid), RiskLevel.MEDIUM)
        state = acc.get_state(sid)
        assert state is not None
        assert state.accumulated_score == Decimal("4.0")  # 1.0 + 3.0

    @pytest.mark.asyncio
    async def test_action_count_increments(self):
        acc = _accumulator()
        sid = uuid4()
        for _ in range(3):
            await acc.assess(sid, _proposal(sid), RiskLevel.LOW)
        state = acc.get_state(sid)
        assert state is not None
        assert state.action_count == 3


# ---------------------------------------------------------------------------
# TestPatternDetection
# ---------------------------------------------------------------------------


class TestPatternDetection:
    @pytest.mark.asyncio
    async def test_complete_sequence_triggers(self):
        acc = _accumulator(patterns=[_exfil_pattern()])
        sid = uuid4()
        await acc.assess(sid, _proposal(sid, "read_crm"), RiskLevel.LOW)
        await acc.assess(sid, _proposal(sid, "query_database"), RiskLevel.MEDIUM)
        result = await acc.assess(sid, _proposal(sid, "send_email"), RiskLevel.LOW)
        assert result.escalated_risk == RiskLevel.HIGH
        assert result.was_escalated is True

    @pytest.mark.asyncio
    async def test_partial_sequence_no_escalation(self):
        acc = _accumulator(patterns=[_exfil_pattern()])
        sid = uuid4()
        await acc.assess(sid, _proposal(sid, "read_crm"), RiskLevel.LOW)
        result = await acc.assess(sid, _proposal(sid, "query_database"), RiskLevel.MEDIUM)
        # Only 2 of 3 steps matched — no escalation from pattern
        assert result.escalated_risk == RiskLevel.MEDIUM
        assert "data_exfiltration" not in result.escalation_reasons

    @pytest.mark.asyncio
    async def test_out_of_order_no_escalation(self):
        acc = _accumulator(patterns=[_exfil_pattern()])
        sid = uuid4()
        # Wrong order: query_database before read_crm
        await acc.assess(sid, _proposal(sid, "query_database"), RiskLevel.MEDIUM)
        await acc.assess(sid, _proposal(sid, "read_crm"), RiskLevel.LOW)
        result = await acc.assess(sid, _proposal(sid, "send_email"), RiskLevel.LOW)
        # Pattern not matched (wrong order)
        assert "Pattern matched: data_exfiltration" not in result.escalation_reasons

    @pytest.mark.asyncio
    async def test_scrolled_out_of_window_no_escalation(self):
        # Pattern window is 3; add 3 intermediate actions to push read_crm out
        pattern = RiskPattern(
            name="tight_window",
            description="test",
            action_sequence=["read_crm", "send_email"],
            window_size=3,
            escalate_to=RiskLevel.HIGH,
        )
        acc = _accumulator(patterns=[pattern])
        sid = uuid4()
        await acc.assess(sid, _proposal(sid, "read_crm"), RiskLevel.LOW)
        # Fill window with 3 other actions, pushing read_crm out
        await acc.assess(sid, _proposal(sid, "other_a"), RiskLevel.LOW)
        await acc.assess(sid, _proposal(sid, "other_b"), RiskLevel.LOW)
        await acc.assess(sid, _proposal(sid, "other_c"), RiskLevel.LOW)
        result = await acc.assess(sid, _proposal(sid, "send_email"), RiskLevel.LOW)
        assert "Pattern matched: tight_window" not in result.escalation_reasons

    @pytest.mark.asyncio
    async def test_multiple_patterns_highest_wins(self):
        pattern_medium = RiskPattern(
            name="mild_chain",
            description="low to medium",
            action_sequence=["read_crm", "query_database"],
            window_size=10,
            escalate_to=RiskLevel.MEDIUM,
        )
        pattern_high = _exfil_pattern()
        acc = _accumulator(patterns=[pattern_medium, pattern_high])
        sid = uuid4()
        await acc.assess(sid, _proposal(sid, "read_crm"), RiskLevel.LOW)
        await acc.assess(sid, _proposal(sid, "query_database"), RiskLevel.MEDIUM)
        result = await acc.assess(sid, _proposal(sid, "send_email"), RiskLevel.LOW)
        # Both patterns match; highest escalation (HIGH) wins
        assert result.escalated_risk == RiskLevel.HIGH

    @pytest.mark.asyncio
    async def test_pattern_name_in_reasons(self):
        acc = _accumulator(patterns=[_exfil_pattern()])
        sid = uuid4()
        await acc.assess(sid, _proposal(sid, "read_crm"), RiskLevel.LOW)
        await acc.assess(sid, _proposal(sid, "query_database"), RiskLevel.MEDIUM)
        result = await acc.assess(sid, _proposal(sid, "send_email"), RiskLevel.LOW)
        assert any("data_exfiltration" in r for r in result.escalation_reasons)


# ---------------------------------------------------------------------------
# TestSessionIsolation
# ---------------------------------------------------------------------------


class TestSessionIsolation:
    @pytest.mark.asyncio
    async def test_independent_scores(self):
        acc = _accumulator()
        sid_a = uuid4()
        sid_b = uuid4()
        # Session A: 5 LOW actions → score 5.0
        for _ in range(5):
            await acc.assess(sid_a, _proposal(sid_a), RiskLevel.LOW)
        # Session B: 1 LOW action → score 1.0
        await acc.assess(sid_b, _proposal(sid_b), RiskLevel.LOW)
        state_b = acc.get_state(sid_b)
        assert state_b is not None
        assert state_b.accumulated_score == Decimal("1.0")

    @pytest.mark.asyncio
    async def test_independent_windows(self):
        acc = _accumulator(patterns=[_exfil_pattern()])
        sid_a = uuid4()
        sid_b = uuid4()
        # Session A: partial exfil sequence
        await acc.assess(sid_a, _proposal(sid_a, "read_crm"), RiskLevel.LOW)
        await acc.assess(sid_a, _proposal(sid_a, "query_database"), RiskLevel.MEDIUM)
        # Session B: only the final action (no prior context)
        result = await acc.assess(sid_b, _proposal(sid_b, "send_email"), RiskLevel.LOW)
        assert "Pattern matched: data_exfiltration" not in result.escalation_reasons


# ---------------------------------------------------------------------------
# TestSessionReset
# ---------------------------------------------------------------------------


class TestSessionReset:
    @pytest.mark.asyncio
    async def test_reset_clears_score(self):
        acc = _accumulator()
        sid = uuid4()
        for _ in range(5):
            await acc.assess(sid, _proposal(sid), RiskLevel.LOW)
        acc.reset_session(sid)
        assert acc.get_state(sid) is None

    @pytest.mark.asyncio
    async def test_reset_clears_actions(self):
        acc = _accumulator(patterns=[_exfil_pattern()])
        sid = uuid4()
        await acc.assess(sid, _proposal(sid, "read_crm"), RiskLevel.LOW)
        await acc.assess(sid, _proposal(sid, "query_database"), RiskLevel.MEDIUM)
        acc.reset_session(sid)
        # After reset, state is gone
        assert acc.get_state(sid) is None

    def test_unknown_session_noop(self):
        acc = _accumulator()
        # reset_session on an unknown session should not raise
        acc.reset_session(uuid4())

    @pytest.mark.asyncio
    async def test_assess_after_reset_starts_fresh(self):
        acc = _accumulator()
        sid = uuid4()
        for _ in range(5):
            await acc.assess(sid, _proposal(sid), RiskLevel.LOW)
        acc.reset_session(sid)
        # One LOW after reset → score 1.0, no escalation
        result = await acc.assess(sid, _proposal(sid), RiskLevel.LOW)
        assert result.session_state.accumulated_score == Decimal("1.0")
        assert result.was_escalated is False


# ---------------------------------------------------------------------------
# TestEventEmission
# ---------------------------------------------------------------------------


class TestEventEmission:
    @pytest.mark.asyncio
    async def test_escalation_emits_session_risk_escalated(self):
        event_repo = InMemoryEventRepository()
        event_store = EventStore(event_repo)
        acc = _accumulator(event_store=event_store)
        sid = uuid4()
        # 5 LOW actions → score 5.0 → MEDIUM escalation on action 5
        for _ in range(4):
            await acc.assess(sid, _proposal(sid), RiskLevel.LOW)
        await acc.assess(sid, _proposal(sid), RiskLevel.LOW)
        events = await event_repo.replay(sid)
        assert any(e.event_kind == EventKind.SESSION_RISK_ESCALATED for e in events)

    @pytest.mark.asyncio
    async def test_no_escalation_emits_nothing(self):
        event_repo = InMemoryEventRepository()
        event_store = EventStore(event_repo)
        acc = _accumulator(event_store=event_store)
        sid = uuid4()
        # 1 LOW action — no escalation
        await acc.assess(sid, _proposal(sid), RiskLevel.LOW)
        events = await event_repo.replay(sid)
        assert not any(e.event_kind == EventKind.SESSION_RISK_ESCALATED for e in events)

    @pytest.mark.asyncio
    async def test_no_event_store_no_error(self):
        # Escalation without event_store configured should not raise
        acc = _accumulator()
        sid = uuid4()
        for _ in range(5):
            result = await acc.assess(sid, _proposal(sid), RiskLevel.LOW)
        # Reaches here without error
        assert result.was_escalated is True
