"""Tests for BudgetTracker budget math and error paths.

Note: increment() uses SQLAlchemy select/update directly which requires real
ORM models. The budget math in increment is identical to check_budget (both
compare used + proposed against max). SQL integration is covered by
examples/quickstart.py.
"""

from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from agent_control_plane.engine.budget_tracker import BudgetTracker


class _FakeSessionRow:
    def __init__(self, *, max_cost, used_cost, max_action_count, used_action_count):
        self.id = uuid4()
        self.max_cost = max_cost
        self.used_cost = used_cost
        self.max_action_count = max_action_count
        self.used_action_count = used_action_count


def _tracker(cs):
    tracker = BudgetTracker()
    tracker._get_session = AsyncMock(return_value=cs)
    return tracker


class TestCheckBudget:
    @pytest.mark.asyncio
    async def test_within_limits(self):
        cs = _FakeSessionRow(max_cost=Decimal("100"), used_cost=Decimal("10"), max_action_count=10, used_action_count=2)
        assert await _tracker(cs).check_budget(AsyncMock(), cs.id, cost=Decimal("50"), action_count=3) is True

    @pytest.mark.asyncio
    async def test_cost_exceeded(self):
        cs = _FakeSessionRow(max_cost=Decimal("100"), used_cost=Decimal("90"), max_action_count=10, used_action_count=2)
        assert await _tracker(cs).check_budget(AsyncMock(), cs.id, cost=Decimal("20"), action_count=1) is False

    @pytest.mark.asyncio
    async def test_count_exceeded(self):
        cs = _FakeSessionRow(max_cost=Decimal("100"), used_cost=Decimal("0"), max_action_count=5, used_action_count=5)
        assert await _tracker(cs).check_budget(AsyncMock(), cs.id, cost=Decimal("1"), action_count=1) is False

    @pytest.mark.asyncio
    async def test_exact_boundary_passes(self):
        cs = _FakeSessionRow(max_cost=Decimal("100"), used_cost=Decimal("90"), max_action_count=10, used_action_count=9)
        assert await _tracker(cs).check_budget(AsyncMock(), cs.id, cost=Decimal("10"), action_count=1) is True

    @pytest.mark.asyncio
    async def test_one_over_boundary_fails(self):
        cs = _FakeSessionRow(max_cost=Decimal("100"), used_cost=Decimal("90"), max_action_count=10, used_action_count=9)
        assert await _tracker(cs).check_budget(AsyncMock(), cs.id, cost=Decimal("10.01"), action_count=1) is False

    @pytest.mark.asyncio
    async def test_zero_cost_at_limit(self):
        cs = _FakeSessionRow(
            max_cost=Decimal("100"), used_cost=Decimal("100"), max_action_count=10, used_action_count=0
        )
        assert await _tracker(cs).check_budget(AsyncMock(), cs.id, cost=Decimal("0"), action_count=1) is True

    @pytest.mark.asyncio
    async def test_both_limits_hit(self):
        cs = _FakeSessionRow(max_cost=Decimal("100"), used_cost=Decimal("100"), max_action_count=5, used_action_count=5)
        assert await _tracker(cs).check_budget(AsyncMock(), cs.id, cost=Decimal("1"), action_count=1) is False


class TestGetRemaining:
    @pytest.mark.asyncio
    async def test_correct_values(self):
        cs = _FakeSessionRow(max_cost=Decimal("100"), used_cost=Decimal("40"), max_action_count=10, used_action_count=3)
        remaining = await _tracker(cs).get_remaining(AsyncMock(), cs.id)
        assert remaining["remaining_cost"] == Decimal("60")
        assert remaining["remaining_count"] == 7
        assert remaining["used_cost"] == Decimal("40")
        assert remaining["used_count"] == 3
        assert remaining["max_cost"] == Decimal("100")
        assert remaining["max_count"] == 10


class TestErrors:
    @pytest.mark.asyncio
    async def test_session_not_found(self):
        tracker = BudgetTracker()
        tracker._get_session = AsyncMock(side_effect=ValueError("Session not found"))
        with pytest.raises(ValueError, match="not found"):
            await tracker.check_budget(AsyncMock(), uuid4(), cost=Decimal("1"))
