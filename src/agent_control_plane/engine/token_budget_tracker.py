"""Identity-scoped, time-windowed token budget enforcement."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from agent_control_plane.types.enums import BudgetPeriod, EventKind
from agent_control_plane.types.token_governance import (
    IdentityContext,
    TokenBudgetCheckResult,
    TokenBudgetState,
    TokenUsage,
)

if TYPE_CHECKING:
    from agent_control_plane.engine.event_store import EventStore
    from agent_control_plane.storage.protocols import AsyncTokenBudgetRepository

logger = logging.getLogger(__name__)


class TokenBudgetExhaustedError(Exception):
    """Raised when an identity's token budget is exhausted."""


def _compute_window(period: BudgetPeriod, now: datetime | None = None) -> tuple[datetime, datetime]:
    """Compute window_start and window_end for the given period."""
    now = now or datetime.now(UTC)
    if period == BudgetPeriod.DAILY:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
    elif period == BudgetPeriod.WEEKLY:
        # Monday midnight UTC
        days_since_monday = now.weekday()
        start = (now - timedelta(days=days_since_monday)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(weeks=1)
    elif period == BudgetPeriod.MONTHLY:
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        # First of next month
        end = start.replace(year=now.year + 1, month=1) if now.month == 12 else start.replace(month=now.month + 1)
    else:
        # UNLIMITED — use a very wide window
        start = datetime(2000, 1, 1, tzinfo=UTC)
        end = datetime(9999, 12, 31, tzinfo=UTC)
    return start, end


class TokenBudgetTracker:
    """Async engine for identity-scoped, time-windowed token budget enforcement."""

    def __init__(
        self,
        token_budget_repo: AsyncTokenBudgetRepository,
        event_store: EventStore | None = None,
    ) -> None:
        self._repo = token_budget_repo
        self._event_store = event_store

    async def check_budget(
        self,
        identity: IdentityContext,
        usage: TokenUsage,
    ) -> TokenBudgetCheckResult:
        """Check if the proposed usage fits within all applicable budgets."""
        configs = await self._repo.list_budget_configs(identity)
        if not configs:
            return TokenBudgetCheckResult(allowed=True)

        denial_reasons: list[str] = []
        last_state: TokenBudgetState | None = None

        for config in configs:
            window_start, window_end = _compute_window(config.period)
            state = await self._repo.get_budget_state(config.id, window_start)

            used_tokens = state.used_tokens if state else 0
            used_cost = state.used_cost_usd if state else usage.estimated_cost_usd.__class__("0")

            # Check token limit
            if config.max_tokens is not None:
                projected = used_tokens + usage.total_tokens
                if projected > config.max_tokens:
                    denial_reasons.append(
                        f"Token limit exceeded for {config.period.value} budget {config.id}: "
                        f"{projected} > {config.max_tokens}"
                    )

            # Check cost limit
            if config.max_cost_usd is not None:
                projected_cost = used_cost + usage.estimated_cost_usd
                if projected_cost > config.max_cost_usd:
                    denial_reasons.append(
                        f"Cost limit exceeded for {config.period.value} budget {config.id}: "
                        f"{projected_cost} > {config.max_cost_usd}"
                    )

            # Check allowed models
            if config.allowed_models is not None and usage.model_id not in config.allowed_models:
                denial_reasons.append(f"Model {usage.model_id} not in allowed models for budget {config.id}")

            # Build state for response
            remaining_tokens = (config.max_tokens - used_tokens) if config.max_tokens is not None else None
            remaining_cost = (config.max_cost_usd - used_cost) if config.max_cost_usd is not None else None
            last_state = TokenBudgetState(
                config_id=config.id,
                identity=identity,
                period=config.period,
                window_start=window_start,
                window_end=window_end,
                used_tokens=used_tokens,
                used_cost_usd=used_cost,
                remaining_tokens=remaining_tokens,
                remaining_cost_usd=remaining_cost,
            )

        return TokenBudgetCheckResult(
            allowed=len(denial_reasons) == 0,
            denial_reasons=denial_reasons,
            budget_state=last_state,
        )

    async def record_usage(
        self,
        session_id: UUID,
        identity: IdentityContext,
        usage: TokenUsage,
    ) -> None:
        """Check budget, record usage, and emit event. Raises on exhaustion."""
        result = await self.check_budget(identity, usage)
        if not result.allowed:
            raise TokenBudgetExhaustedError("; ".join(result.denial_reasons))

        configs = await self._repo.list_budget_configs(identity)
        for config in configs:
            window_start, window_end = _compute_window(config.period)
            await self._repo.increment_usage(
                config.id, window_start, window_end, usage.total_tokens, usage.estimated_cost_usd
            )

        await self._repo.record_usage(session_id, usage, identity)

        if self._event_store is not None:
            await self._event_store.append(
                session_id=session_id,
                event_kind=EventKind.TOKEN_USAGE_RECORDED,
                payload={
                    "model_id": str(usage.model_id),
                    "total_tokens": usage.total_tokens,
                    "estimated_cost_usd": str(usage.estimated_cost_usd),
                    "user_id": str(identity.user_id) if identity.user_id else None,
                    "org_id": str(identity.org_id) if identity.org_id else None,
                },
            )

    async def get_budget_states(self, identity: IdentityContext) -> list[TokenBudgetState]:
        """Get current budget states for all configs matching the identity."""
        configs = await self._repo.list_budget_configs(identity)
        states: list[TokenBudgetState] = []
        for config in configs:
            window_start, window_end = _compute_window(config.period)
            state = await self._repo.get_budget_state(config.id, window_start)
            if state is not None:
                states.append(state)
            else:
                remaining_tokens = config.max_tokens if config.max_tokens is not None else None
                remaining_cost = config.max_cost_usd if config.max_cost_usd is not None else None
                states.append(
                    TokenBudgetState(
                        config_id=config.id,
                        identity=identity,
                        period=config.period,
                        window_start=window_start,
                        window_end=window_end,
                        used_tokens=0,
                        used_cost_usd=Decimal("0"),
                        remaining_tokens=remaining_tokens,
                        remaining_cost_usd=remaining_cost,
                    )
                )
        return states
