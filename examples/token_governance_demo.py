"""Token governance demo.

Demonstrates:
1. Model access policy: tier-based model restrictions and identity overrides.
2. Token budget enforcement: daily budget with token and cost limits.
3. Budget exhaustion: usage recording until budget is exceeded.
4. Event emission: TOKEN_USAGE_RECORDED telemetry events.

Run:
    uv run python examples/token_governance_demo.py
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from agent_control_plane.engine.event_store import EventStore
from agent_control_plane.engine.model_governor import ModelGovernor
from agent_control_plane.engine.token_budget_tracker import (
    TokenBudgetExhaustedError,
    TokenBudgetTracker,
)
from agent_control_plane.types.enums import ActionTier, BudgetPeriod, EventKind, ModelTier
from agent_control_plane.types.frames import EventFrame
from agent_control_plane.types.ids import ModelId, OrgId, TeamId, UserId
from agent_control_plane.types.token_governance import (
    IdentityContext,
    ModelGovernancePolicy,
    TokenBudgetConfig,
    TokenBudgetState,
    TokenUsage,
    TokenUsageSummary,
)

# ---------------------------------------------------------------------------
# Minimal in-memory repo (self-contained, no test dependency)
# ---------------------------------------------------------------------------


class _InMemoryTokenBudgetRepo:
    """Minimal in-memory token budget repo for demo purposes."""

    def __init__(self) -> None:
        self._configs: dict[UUID, TokenBudgetConfig] = {}
        self._states: dict[tuple[UUID, datetime], TokenBudgetState] = {}
        self._usage_records: list[dict[str, Any]] = []

    async def get_budget_config(self, config_id: UUID) -> TokenBudgetConfig | None:
        return self._configs.get(config_id)

    async def list_budget_configs(self, identity: IdentityContext) -> list[TokenBudgetConfig]:
        results: list[TokenBudgetConfig] = []
        for config in self._configs.values():
            ci = config.identity
            if ci.user_id is not None and ci.user_id != identity.user_id:
                continue
            if ci.org_id is not None and ci.org_id != identity.org_id:
                continue
            if ci.team_id is not None and ci.team_id != identity.team_id:
                continue
            results.append(config)
        return results

    async def create_budget_config(self, config: TokenBudgetConfig) -> TokenBudgetConfig:
        self._configs[config.id] = config
        return config

    async def get_budget_state(self, config_id: UUID, window_start: datetime) -> TokenBudgetState | None:
        return self._states.get((config_id, window_start))

    async def increment_usage(
        self, config_id: UUID, window_start: datetime, window_end: datetime, tokens: int, cost_usd: Decimal
    ) -> TokenBudgetState:
        key = (config_id, window_start)
        config = self._configs[config_id]
        existing = self._states.get(key)
        used_tokens = (existing.used_tokens + tokens) if existing else tokens
        used_cost = (existing.used_cost_usd + cost_usd) if existing else cost_usd
        remaining_tokens = (config.max_tokens - used_tokens) if config.max_tokens is not None else None
        remaining_cost = (config.max_cost_usd - used_cost) if config.max_cost_usd is not None else None
        state = TokenBudgetState(
            config_id=config_id,
            identity=config.identity,
            period=config.period,
            window_start=window_start,
            window_end=window_end,
            used_tokens=used_tokens,
            used_cost_usd=used_cost,
            remaining_tokens=remaining_tokens,
            remaining_cost_usd=remaining_cost,
        )
        self._states[key] = state
        return state

    async def record_usage(self, session_id: UUID, usage: TokenUsage, identity: IdentityContext) -> None:
        self._usage_records.append({"session_id": session_id, "usage": usage, "identity": identity})

    async def get_usage_summary(
        self, identity: IdentityContext, period: BudgetPeriod, window_start: datetime
    ) -> TokenUsageSummary | None:
        return None


class _InMemoryEventRepo:
    """Minimal in-memory event repo for demo event emission."""

    def __init__(self) -> None:
        self._events: dict[UUID, list[EventFrame]] = {}
        self._seq: dict[UUID, int] = {}

    async def append(
        self,
        session_id: UUID,
        event_kind: EventKind,
        payload: dict[str, Any],
        *,
        state_bearing: bool = False,
        agent_id: str | None = None,
        correlation_id: UUID | None = None,
        routing_decision: dict[str, Any] | None = None,
        routing_reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> int:
        seq = self._seq.get(session_id, 1)
        self._seq[session_id] = seq + 1
        event = EventFrame(session_id=session_id, seq=seq, event_kind=event_kind, payload=payload)
        self._events.setdefault(session_id, []).append(event)
        return seq

    async def replay(self, session_id: UUID, after_seq: int = 0, limit: int = 100) -> list[EventFrame]:
        return [e for e in self._events.get(session_id, []) if e.seq > after_seq][:limit]

    async def get_last_event(self, session_id: UUID) -> EventFrame | None:
        events = self._events.get(session_id, [])
        return events[-1] if events else None


# ---------------------------------------------------------------------------
# Demos
# ---------------------------------------------------------------------------


def demo_model_governor() -> None:
    print("\n=== 1. Model access policy ===")
    policy = ModelGovernancePolicy(
        model_tier_assignments={
            "gpt-4o": ModelTier.PREMIUM,
            "gpt-4o-mini": ModelTier.STANDARD,
            "claude-opus": ModelTier.RESTRICTED,
        },
        tier_restrictions={
            "auto_approve": ["standard"],
            "always_approve": ["standard", "premium"],
            "unrestricted": ["standard", "premium", "restricted"],
        },
        identity_overrides={
            "admin-alice": ["gpt-4o", "claude-opus", "gpt-4o-mini"],
        },
    )
    governor = ModelGovernor(policy)

    # Standard user: gpt-4o-mini allowed for auto_approve, gpt-4o denied
    print("  Standard user, auto_approve tier:")
    for model in ["gpt-4o-mini", "gpt-4o", "claude-opus"]:
        result = governor.check_access(ModelId(model), ActionTier.AUTO_APPROVE)
        status = "ALLOWED" if result.allowed else f"DENIED ({result.denial_reason})"
        print(f"    {model}: {status}")

    # Admin override: all models allowed regardless of tier
    print("  Admin user (identity override):")
    admin = IdentityContext(user_id=UserId("admin-alice"))
    for model in ["gpt-4o-mini", "gpt-4o", "claude-opus"]:
        result = governor.check_access(ModelId(model), ActionTier.AUTO_APPROVE, admin)
        status = "ALLOWED" if result.allowed else f"DENIED ({result.denial_reason})"
        print(f"    {model}: {status}")

    # List allowed models per tier
    print("  Allowed models by action tier:")
    for tier in [ActionTier.AUTO_APPROVE, ActionTier.ALWAYS_APPROVE, ActionTier.UNRESTRICTED]:
        allowed = governor.get_allowed_models(tier)
        print(f"    {tier.value}: {[str(m) for m in allowed]}")


async def demo_token_budget() -> None:
    print("\n=== 2. Token budget enforcement ===")
    repo = _InMemoryTokenBudgetRepo()
    tracker = TokenBudgetTracker(repo)

    identity = IdentityContext(
        user_id=UserId("dev-bob"),
        org_id=OrgId("acme-corp"),
        team_id=TeamId("ml-team"),
    )

    # Create a daily budget: 1000 tokens, $0.10 max
    config = TokenBudgetConfig(
        identity=identity,
        period=BudgetPeriod.DAILY,
        max_tokens=1000,
        max_cost_usd=Decimal("0.10"),
        allowed_models=[ModelId("gpt-4o-mini"), ModelId("gpt-4o")],
    )
    await repo.create_budget_config(config)

    # Check budget before usage
    usage = TokenUsage(
        model_id=ModelId("gpt-4o-mini"),
        input_tokens=100,
        output_tokens=50,
        total_tokens=150,
        estimated_cost_usd=Decimal("0.002"),
    )
    result = await tracker.check_budget(identity, usage)
    print(f"  Budget check (150 tokens, $0.002): allowed={result.allowed}")
    if result.budget_states:
        state = result.budget_states[0]
        print(f"    remaining_tokens={state.remaining_tokens}  remaining_cost=${state.remaining_cost_usd}")

    # Record usage
    session_id = uuid4()
    await tracker.record_usage(session_id, identity, usage)
    print(f"  Recorded 150 tokens for session {session_id}")

    # Check state after usage
    states = await tracker.get_budget_states(identity)
    if states:
        s = states[0]
        print(f"  After recording: used_tokens={s.used_tokens}  remaining_tokens={s.remaining_tokens}")

    # Check with disallowed model
    bad_usage = TokenUsage(
        model_id=ModelId("claude-opus"),
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        estimated_cost_usd=Decimal("0.001"),
    )
    result = await tracker.check_budget(identity, bad_usage)
    print(f"  Budget check (claude-opus, not in allowed_models): allowed={result.allowed}")
    for reason in result.denial_reasons:
        print(f"    reason: {reason}")


async def demo_budget_exhaustion() -> None:
    print("\n=== 3. Budget exhaustion ===")
    repo = _InMemoryTokenBudgetRepo()
    tracker = TokenBudgetTracker(repo)

    identity = IdentityContext(user_id=UserId("dev-carol"), org_id=OrgId("acme-corp"))
    config = TokenBudgetConfig(
        identity=identity,
        period=BudgetPeriod.DAILY,
        max_tokens=500,
    )
    await repo.create_budget_config(config)

    session_id = uuid4()
    usage = TokenUsage(
        model_id=ModelId("gpt-4o-mini"),
        input_tokens=100,
        output_tokens=100,
        total_tokens=200,
        estimated_cost_usd=Decimal("0.003"),
    )

    # Record twice (400 tokens total, within budget)
    await tracker.record_usage(session_id, identity, usage)
    await tracker.record_usage(session_id, identity, usage)
    print("  Recorded 400 tokens (2 x 200), budget=500")

    # Third attempt should fail (600 > 500)
    try:
        await tracker.record_usage(session_id, identity, usage)
        print("  ERROR: should have raised")
    except TokenBudgetExhaustedError as exc:
        print(f"  Third request (600 > 500) correctly denied: {exc}")


async def demo_event_emission() -> None:
    print("\n=== 4. Event emission ===")
    repo = _InMemoryTokenBudgetRepo()
    event_repo = _InMemoryEventRepo()
    event_store = EventStore(event_repo)
    tracker = TokenBudgetTracker(repo, event_store=event_store)

    identity = IdentityContext(user_id=UserId("dev-dave"), org_id=OrgId("acme-corp"))
    config = TokenBudgetConfig(
        identity=identity,
        period=BudgetPeriod.DAILY,
        max_tokens=10000,
    )
    await repo.create_budget_config(config)

    session_id = uuid4()
    usage = TokenUsage(
        model_id=ModelId("gpt-4o"),
        input_tokens=500,
        output_tokens=200,
        total_tokens=700,
        estimated_cost_usd=Decimal("0.02"),
    )
    await tracker.record_usage(session_id, identity, usage)

    events = await event_repo.replay(session_id)
    token_events = [e for e in events if e.event_kind == EventKind.TOKEN_USAGE_RECORDED]
    print(f"  TOKEN_USAGE_RECORDED events emitted: {len(token_events)}")
    for e in token_events:
        print(f"    payload: {e.payload}")


async def main() -> None:
    print("Token governance demo — agent-control-plane v0.11.0")
    demo_model_governor()
    await demo_token_budget()
    await demo_budget_exhaustion()
    await demo_event_emission()
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
