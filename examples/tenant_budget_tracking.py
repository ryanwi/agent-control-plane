"""Per-tenant LLM cost ceiling — the v0.14.3 ergonomics, end-to-end.

Demonstrates the smallest meaningful integration shape: a host app that
makes LLM calls on behalf of tenants and wants persistent, per-tenant
daily $ caps. No control-plane sessions, no event sourcing, no proposal
lifecycle — just budget tracking.

Shows:
1. ControlPlaneSetup → AsyncResilientControlPlane (one-stop builder).
2. Seeding a TokenBudgetConfig per tenant via the repo directly.
3. cp.token_budget_tracker() async context manager — fresh session,
   commits on clean exit, rolls back on exception.
4. Sessionless record_usage(None, identity, usage) — for hosts that
   track budgets outside the control-plane session machinery.
5. Float cost coercion — passes a float, validator routes through
   Decimal(str(...)) to preserve precision.
6. Identity by string slug — OrgId is a str, not a UUID.
7. Budget exhaustion path — TokenBudgetExhaustedError raised.
8. Persistence — second run picks up where the first left off.

Run:
    uv run python examples/tenant_budget_tracking.py
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from agent_control_plane import (
    AsyncSqlAlchemyTokenBudgetRepo,
    ControlPlaneSetup,
    IdentityContext,
    ModelId,
    OrgId,
    TokenBudgetConfig,
    TokenBudgetExhaustedError,
    TokenUsage,
)
from agent_control_plane.types.enums import BudgetPeriod


def make_identity(tenant_slug: str) -> IdentityContext:
    """Map a host-app tenant slug to a CP identity. OrgId is a str alias."""
    return IdentityContext(org_id=OrgId(tenant_slug))


async def seed_tenant_budget(cp, tenant_slug: str, daily_cost_usd: float) -> None:
    """One-shot seeding. Uses the repo directly — register_models() is
    lazy-called inside the repo (v0.14.3) so this works without first
    building the facade for any other reason."""
    async with cp.facade.session_scope() as db:
        repo = AsyncSqlAlchemyTokenBudgetRepo(db)
        await repo.create_budget_config(
            TokenBudgetConfig(
                identity=make_identity(tenant_slug),
                period=BudgetPeriod.DAILY,
                max_cost_usd=daily_cost_usd,  # float accepted (v0.14.3)
            )
        )
        await db.commit()


async def simulate_llm_call(cp, tenant_slug: str, *, cost_usd: float) -> None:
    """The shape every consumer's LLM client wraps. Open a budget-tracker
    session, record usage post-call, let the SDK raise on exhaustion."""
    async with cp.token_budget_tracker() as tracker:
        usage = TokenUsage(
            model_id=ModelId("claude-haiku-4-5"),
            input_tokens=1000,
            output_tokens=500,
            total_tokens=1500,
            estimated_cost_usd=cost_usd,  # float — validator coerces
        )
        await tracker.record_usage(None, make_identity(tenant_slug), usage)


async def show_budget_state(cp, tenant_slug: str) -> None:
    async with cp.token_budget_tracker() as tracker:
        states = await tracker.get_budget_states(make_identity(tenant_slug))
        for state in states:
            cap = state.used_cost_usd + (state.remaining_cost_usd or 0)
            print(f"  {tenant_slug}: used ${state.used_cost_usd} of ${cap} ({state.used_tokens} tokens)")


async def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    db_url = f"sqlite+aiosqlite:///{tmp}/tenant_budgets.db"
    cp = ControlPlaneSetup(db_url).build_async()

    print("=== Seeding budgets ===")
    await seed_tenant_budget(cp, "tenant-acme", daily_cost_usd=0.10)
    await seed_tenant_budget(cp, "tenant-globex", daily_cost_usd=0.50)

    print("\n=== Recording usage (within budget) ===")
    await simulate_llm_call(cp, "tenant-acme", cost_usd=0.03)
    await simulate_llm_call(cp, "tenant-acme", cost_usd=0.04)
    await simulate_llm_call(cp, "tenant-globex", cost_usd=0.20)
    await show_budget_state(cp, "tenant-acme")
    await show_budget_state(cp, "tenant-globex")

    print("\n=== Pushing tenant-acme over the cap ===")
    try:
        await simulate_llm_call(cp, "tenant-acme", cost_usd=0.05)  # 0.07 + 0.05 = 0.12 > 0.10
    except TokenBudgetExhaustedError as e:
        print(f"  blocked: {e}")

    print("\n=== Restart simulation ===")
    await cp.close()
    cp2 = ControlPlaneSetup(db_url).build_async()
    print("  (new control-plane instance, same DB)")
    await show_budget_state(cp2, "tenant-acme")
    try:
        await simulate_llm_call(cp2, "tenant-acme", cost_usd=0.05)
    except TokenBudgetExhaustedError as e:
        print(f"  still blocked: {e}")
    await cp2.close()


if __name__ == "__main__":
    asyncio.run(main())
