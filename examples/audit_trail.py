"""Tier 2 — audit trail with session tracking.

Records that an agent run happened, how long it took, and what it cost.
No policy engine, no approval gates, no kill switch — just open a run,
do work, and close it. The session row and its events form the audit record.

Shows:
1. cp.run() async context manager — open, activate, close in one call.
2. run.tag() — attach metadata written into the close payload.
3. token_budget_tracker() — record token usage against the session.
4. Error path — exception aborts the session and re-raises.

Run:
    uv run python examples/audit_trail.py
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from agent_control_plane import (
    ControlPlaneSetup,
    IdentityContext,
    ModelId,
    OrgId,
    TokenUsage,
)
from agent_control_plane.types.enums import ExecutionMode


async def simulate_work(cp, session_id, *, input_tokens: int, output_tokens: int) -> None:
    """Record token usage against an open session."""
    async with cp.token_budget_tracker() as tracker:
        await tracker.record_usage(
            session_id,
            IdentityContext(org_id=OrgId("default")),
            TokenUsage(
                model_id=ModelId("claude-sonnet-4-6"),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                estimated_cost_usd=round((input_tokens * 3 + output_tokens * 15) / 1_000_000, 6),
            ),
        )


async def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    db_url = f"sqlite+aiosqlite:///{tmp}/audit.db"
    cp = ControlPlaneSetup(db_url).build_async()

    print("=== Successful run ===")
    async with cp.run("editorial-generation:run-1", execution_mode=ExecutionMode.LIVE) as run:
        run.tag(tenant="acme", model="claude-sonnet-4-6", pipeline_version="1.2")
        await simulate_work(cp, run.session_id, input_tokens=2000, output_tokens=800)
        print(f"  session_id={run.session_id}")
    print("  run closed: COMPLETED")

    print("\n=== Failed run ===")
    try:
        async with cp.run("editorial-generation:run-2", execution_mode=ExecutionMode.LIVE) as run:
            run.tag(tenant="acme", pipeline_version="1.2")
            raise RuntimeError("upstream API timeout")
    except RuntimeError as e:
        print(f"  caught: {e}")
    print("  run closed: ABORTED (abort_reason in close payload)")

    await cp.close()


if __name__ == "__main__":
    asyncio.run(main())
