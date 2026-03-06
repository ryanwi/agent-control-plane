"""Tests for BudgetTracker budget math and error paths."""

from decimal import Decimal
from uuid import uuid4

import pytest

from agent_control_plane.engine.budget_tracker import BudgetExhaustedError, BudgetTracker
from agent_control_plane.types.enums import SessionStatus

from .fakes import InMemorySessionRepository


async def _make_tracker(*, max_cost, used_cost, max_action_count, used_action_count):
    repo = InMemorySessionRepository()
    cs = await repo.create_session(
        session_name=f"test-{uuid4()}",
        status=SessionStatus.ACTIVE,
        execution_mode="dry_run",
        max_cost=max_cost,
        used_cost=used_cost,
        max_action_count=max_action_count,
        used_action_count=used_action_count,
    )
    tracker = BudgetTracker(repo)
    return tracker, cs.id


class TestCheckBudget:
    @pytest.mark.asyncio
    async def test_within_limits(self):
        tracker, sid = await _make_tracker(
            max_cost=Decimal("100"), used_cost=Decimal("10"), max_action_count=10, used_action_count=2
        )
        assert await tracker.check_budget(sid, cost=Decimal("50"), action_count=3) is True

    @pytest.mark.asyncio
    async def test_cost_exceeded(self):
        tracker, sid = await _make_tracker(
            max_cost=Decimal("100"), used_cost=Decimal("90"), max_action_count=10, used_action_count=2
        )
        assert await tracker.check_budget(sid, cost=Decimal("20"), action_count=1) is False

    @pytest.mark.asyncio
    async def test_count_exceeded(self):
        tracker, sid = await _make_tracker(
            max_cost=Decimal("100"), used_cost=Decimal("0"), max_action_count=5, used_action_count=5
        )
        assert await tracker.check_budget(sid, cost=Decimal("1"), action_count=1) is False

    @pytest.mark.asyncio
    async def test_exact_boundary_passes(self):
        tracker, sid = await _make_tracker(
            max_cost=Decimal("100"), used_cost=Decimal("90"), max_action_count=10, used_action_count=9
        )
        assert await tracker.check_budget(sid, cost=Decimal("10"), action_count=1) is True

    @pytest.mark.asyncio
    async def test_one_over_boundary_fails(self):
        tracker, sid = await _make_tracker(
            max_cost=Decimal("100"), used_cost=Decimal("90"), max_action_count=10, used_action_count=9
        )
        assert await tracker.check_budget(sid, cost=Decimal("10.01"), action_count=1) is False

    @pytest.mark.asyncio
    async def test_zero_cost_at_limit(self):
        tracker, sid = await _make_tracker(
            max_cost=Decimal("100"), used_cost=Decimal("100"), max_action_count=10, used_action_count=0
        )
        assert await tracker.check_budget(sid, cost=Decimal("0"), action_count=1) is True

    @pytest.mark.asyncio
    async def test_both_limits_hit(self):
        tracker, sid = await _make_tracker(
            max_cost=Decimal("100"), used_cost=Decimal("100"), max_action_count=5, used_action_count=5
        )
        assert await tracker.check_budget(sid, cost=Decimal("1"), action_count=1) is False


class TestGetRemaining:
    @pytest.mark.asyncio
    async def test_correct_values(self):
        tracker, sid = await _make_tracker(
            max_cost=Decimal("100"), used_cost=Decimal("40"), max_action_count=10, used_action_count=3
        )
        remaining = await tracker.get_remaining(sid)
        assert remaining.remaining_cost == Decimal("60")
        assert remaining.remaining_count == 7
        assert remaining.used_cost == Decimal("40")
        assert remaining.used_count == 3
        assert remaining.max_cost == Decimal("100")
        assert remaining.max_count == 10


class TestIncrement:
    @pytest.mark.asyncio
    async def test_increment_within_limits(self):
        tracker, sid = await _make_tracker(
            max_cost=Decimal("100"), used_cost=Decimal("0"), max_action_count=10, used_action_count=0
        )
        await tracker.increment(sid, cost=Decimal("50"), action_count=3)
        remaining = await tracker.get_remaining(sid)
        assert remaining.used_cost == Decimal("50")
        assert remaining.used_count == 3

    @pytest.mark.asyncio
    async def test_increment_exceeds_budget(self):
        tracker, sid = await _make_tracker(
            max_cost=Decimal("100"), used_cost=Decimal("90"), max_action_count=10, used_action_count=0
        )
        with pytest.raises(BudgetExhaustedError):
            await tracker.increment(sid, cost=Decimal("20"), action_count=1)


class TestErrors:
    @pytest.mark.asyncio
    async def test_session_not_found(self):
        repo = InMemorySessionRepository()
        tracker = BudgetTracker(repo)
        with pytest.raises(ValueError, match="not found"):
            await tracker.check_budget(uuid4(), cost=Decimal("1"))
