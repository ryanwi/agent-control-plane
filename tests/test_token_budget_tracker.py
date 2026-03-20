"""Tests for TokenBudgetTracker engine."""

from decimal import Decimal
from uuid import uuid4

import pytest

from agent_control_plane.engine.token_budget_tracker import (
    TokenBudgetExhaustedError,
    TokenBudgetTracker,
    _compute_window,
)
from agent_control_plane.types.enums import BudgetPeriod, EventKind
from agent_control_plane.types.ids import ModelId, OrgId, TeamId, UserId
from agent_control_plane.types.token_governance import (
    IdentityContext,
    TokenBudgetConfig,
    TokenUsage,
)

from .fakes import InMemoryEventRepository, InMemoryTokenBudgetRepository


def _make_usage(
    model_id: str = "gpt-4",
    input_tokens: int = 100,
    output_tokens: int = 50,
    total_tokens: int = 150,
    cost: str = "0.01",
) -> TokenUsage:
    return TokenUsage(
        model_id=ModelId(model_id),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        estimated_cost_usd=Decimal(cost),
    )


def _make_identity(
    user_id: str | None = "user-1",
    org_id: str | None = "org-1",
    team_id: str | None = None,
) -> IdentityContext:
    return IdentityContext(
        user_id=UserId(user_id) if user_id else None,
        org_id=OrgId(org_id) if org_id else None,
        team_id=TeamId(team_id) if team_id else None,
    )


@pytest.fixture
def repo() -> InMemoryTokenBudgetRepository:
    return InMemoryTokenBudgetRepository()


@pytest.fixture
def event_repo() -> InMemoryEventRepository:
    return InMemoryEventRepository()


@pytest.fixture
def tracker(repo: InMemoryTokenBudgetRepository) -> TokenBudgetTracker:
    return TokenBudgetTracker(repo)


class TestComputeWindow:
    def test_daily_window(self) -> None:
        from datetime import UTC, datetime

        now = datetime(2026, 3, 19, 14, 30, 0, tzinfo=UTC)
        start, end = _compute_window(BudgetPeriod.DAILY, now)
        assert start.hour == 0 and start.minute == 0
        assert start.day == 19
        assert end.day == 20

    def test_weekly_window_monday(self) -> None:
        from datetime import UTC, datetime

        # 2026-03-19 is a Thursday
        now = datetime(2026, 3, 19, 14, 30, 0, tzinfo=UTC)
        start, _end = _compute_window(BudgetPeriod.WEEKLY, now)
        assert start.weekday() == 0  # Monday
        assert start.day == 16

    def test_monthly_window(self) -> None:
        from datetime import UTC, datetime

        now = datetime(2026, 3, 19, 14, 30, 0, tzinfo=UTC)
        start, end = _compute_window(BudgetPeriod.MONTHLY, now)
        assert start.day == 1
        assert end.month == 4

    def test_monthly_december(self) -> None:
        from datetime import UTC, datetime

        now = datetime(2026, 12, 15, 0, 0, 0, tzinfo=UTC)
        _start, end = _compute_window(BudgetPeriod.MONTHLY, now)
        assert end.year == 2027
        assert end.month == 1

    def test_unlimited_window(self) -> None:
        start, end = _compute_window(BudgetPeriod.UNLIMITED)
        assert start.year == 2000
        assert end.year == 9999


class TestCheckBudget:
    async def test_no_configs_allows(self, tracker: TokenBudgetTracker) -> None:
        result = await tracker.check_budget(_make_identity(), _make_usage())
        assert result.allowed is True

    async def test_within_token_limit(self, tracker: TokenBudgetTracker, repo: InMemoryTokenBudgetRepository) -> None:
        identity = _make_identity()
        config = TokenBudgetConfig(
            identity=identity,
            period=BudgetPeriod.DAILY,
            max_tokens=1000,
        )
        await repo.create_budget_config(config)
        result = await tracker.check_budget(identity, _make_usage(total_tokens=100))
        assert result.allowed is True

    async def test_exceeds_token_limit(self, tracker: TokenBudgetTracker, repo: InMemoryTokenBudgetRepository) -> None:
        identity = _make_identity()
        config = TokenBudgetConfig(
            identity=identity,
            period=BudgetPeriod.DAILY,
            max_tokens=100,
        )
        await repo.create_budget_config(config)
        result = await tracker.check_budget(identity, _make_usage(total_tokens=200))
        assert result.allowed is False
        assert len(result.denial_reasons) == 1
        assert "Token limit" in result.denial_reasons[0]

    async def test_exceeds_cost_limit(self, tracker: TokenBudgetTracker, repo: InMemoryTokenBudgetRepository) -> None:
        identity = _make_identity()
        config = TokenBudgetConfig(
            identity=identity,
            period=BudgetPeriod.DAILY,
            max_cost_usd=Decimal("0.005"),
        )
        await repo.create_budget_config(config)
        result = await tracker.check_budget(identity, _make_usage(cost="0.01"))
        assert result.allowed is False
        assert "Cost limit" in result.denial_reasons[0]

    async def test_model_not_allowed(self, tracker: TokenBudgetTracker, repo: InMemoryTokenBudgetRepository) -> None:
        identity = _make_identity()
        config = TokenBudgetConfig(
            identity=identity,
            period=BudgetPeriod.DAILY,
            allowed_models=[ModelId("gpt-3.5")],
        )
        await repo.create_budget_config(config)
        result = await tracker.check_budget(identity, _make_usage(model_id="gpt-4"))
        assert result.allowed is False
        assert "not in allowed models" in result.denial_reasons[0]

    async def test_budget_state_returned(
        self, tracker: TokenBudgetTracker, repo: InMemoryTokenBudgetRepository
    ) -> None:
        identity = _make_identity()
        config = TokenBudgetConfig(
            identity=identity,
            period=BudgetPeriod.DAILY,
            max_tokens=1000,
        )
        await repo.create_budget_config(config)
        result = await tracker.check_budget(identity, _make_usage())
        assert len(result.budget_states) == 1
        assert result.budget_states[0].remaining_tokens == 1000


class TestRecordUsage:
    async def test_record_within_budget(self, tracker: TokenBudgetTracker, repo: InMemoryTokenBudgetRepository) -> None:
        identity = _make_identity()
        config = TokenBudgetConfig(
            identity=identity,
            period=BudgetPeriod.DAILY,
            max_tokens=1000,
        )
        await repo.create_budget_config(config)
        session_id = uuid4()
        await tracker.record_usage(session_id, identity, _make_usage(total_tokens=100))
        assert len(repo._usage_records) == 1

    async def test_record_exceeds_budget_raises(
        self, tracker: TokenBudgetTracker, repo: InMemoryTokenBudgetRepository
    ) -> None:
        identity = _make_identity()
        config = TokenBudgetConfig(
            identity=identity,
            period=BudgetPeriod.DAILY,
            max_tokens=50,
        )
        await repo.create_budget_config(config)
        session_id = uuid4()
        with pytest.raises(TokenBudgetExhaustedError):
            await tracker.record_usage(session_id, identity, _make_usage(total_tokens=100))

    async def test_record_increments_state(
        self, tracker: TokenBudgetTracker, repo: InMemoryTokenBudgetRepository
    ) -> None:
        identity = _make_identity()
        config = TokenBudgetConfig(
            identity=identity,
            period=BudgetPeriod.DAILY,
            max_tokens=1000,
        )
        await repo.create_budget_config(config)
        session_id = uuid4()
        await tracker.record_usage(session_id, identity, _make_usage(total_tokens=100))
        await tracker.record_usage(session_id, identity, _make_usage(total_tokens=200))

        states = await tracker.get_budget_states(identity)
        assert len(states) == 1
        assert states[0].used_tokens == 300

    async def test_record_emits_event(
        self, repo: InMemoryTokenBudgetRepository, event_repo: InMemoryEventRepository
    ) -> None:
        from agent_control_plane.engine.event_store import EventStore

        event_store = EventStore(event_repo)
        tracker = TokenBudgetTracker(repo, event_store=event_store)

        identity = _make_identity()
        config = TokenBudgetConfig(
            identity=identity,
            period=BudgetPeriod.DAILY,
            max_tokens=1000,
        )
        await repo.create_budget_config(config)
        session_id = uuid4()
        await tracker.record_usage(session_id, identity, _make_usage())

        events = await event_repo.replay(session_id)
        assert len(events) == 1
        assert events[0].event_kind == EventKind.TOKEN_USAGE_RECORDED


class TestIdentityMatching:
    async def test_org_level_config_matches_user(
        self, tracker: TokenBudgetTracker, repo: InMemoryTokenBudgetRepository
    ) -> None:
        """An org-level config should match any user in that org."""
        org_config = TokenBudgetConfig(
            identity=IdentityContext(org_id=OrgId("org-1")),
            period=BudgetPeriod.DAILY,
            max_tokens=500,
        )
        await repo.create_budget_config(org_config)

        user_identity = _make_identity(user_id="user-1", org_id="org-1")
        result = await tracker.check_budget(user_identity, _make_usage(total_tokens=600))
        assert result.allowed is False

    async def test_different_org_not_matched(
        self, tracker: TokenBudgetTracker, repo: InMemoryTokenBudgetRepository
    ) -> None:
        org_config = TokenBudgetConfig(
            identity=IdentityContext(org_id=OrgId("org-1")),
            period=BudgetPeriod.DAILY,
            max_tokens=500,
        )
        await repo.create_budget_config(org_config)

        other_identity = _make_identity(user_id="user-2", org_id="org-2")
        result = await tracker.check_budget(other_identity, _make_usage(total_tokens=600))
        assert result.allowed is True  # config doesn't match


class TestGetBudgetStates:
    async def test_returns_empty_for_no_configs(self, tracker: TokenBudgetTracker) -> None:
        states = await tracker.get_budget_states(_make_identity())
        assert states == []

    async def test_returns_zero_state_for_unused_config(
        self, tracker: TokenBudgetTracker, repo: InMemoryTokenBudgetRepository
    ) -> None:
        identity = _make_identity()
        config = TokenBudgetConfig(
            identity=identity,
            period=BudgetPeriod.DAILY,
            max_tokens=1000,
        )
        await repo.create_budget_config(config)
        states = await tracker.get_budget_states(identity)
        assert len(states) == 1
        assert states[0].used_tokens == 0
        assert states[0].remaining_tokens == 1000
