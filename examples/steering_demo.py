"""Steering action demo.

Demonstrates:
1. Configuring actions for steering instead of blocking.
2. SteeringContext with corrective guidance and suggested alternatives.
3. MCP gateway raising SteeringRequiredError with full context.
4. Routing decisions that carry steering metadata.

Run:
    uv run python examples/steering_demo.py
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path

from agent_control_plane.engine.policy_engine import PolicyEngine
from agent_control_plane.engine.router import ProposalRouter
from agent_control_plane.mcp import (
    McpGateway,
    McpGatewayConfig,
    SteeringRequiredError,
    ToolCallContext,
    ToolCallResult,
    ToolPolicyMap,
)
from agent_control_plane.sync import SyncControlPlane
from agent_control_plane.types.enums import ActionName, ActionTier
from agent_control_plane.types.policies import ActionTiers, PolicySnapshot
from agent_control_plane.types.proposals import ActionProposal

# ── 1. Policy with steering tier ────────────────────────────────

POLICY = PolicySnapshot(
    action_tiers=ActionTiers(
        blocked=[ActionName.WIPE_DATABASE],
        always_approve=[ActionName.WIRE_TRANSFER],
        auto_approve=[ActionName.STATUS, ActionName.CHECK_BALANCE],
        steer=[ActionName.CHANGE_ADDRESS, ActionName.RESET_PASSWORD],
    ),
)


# ── 2. Direct routing: see SteeringContext on the decision ──────


async def demo_routing() -> None:
    print("=== 1. ProposalRouter with steering ===\n")

    engine = PolicyEngine(POLICY)
    router = ProposalRouter(engine)

    for action in [ActionName.STATUS, ActionName.CHANGE_ADDRESS, ActionName.WIPE_DATABASE]:
        proposal = ActionProposal(
            session_id="00000000-0000-0000-0000-000000000001",
            resource_id="user-42",
            resource_type="account",
            decision=action,
            reasoning="demo",
            weight=Decimal("1.0"),
            score=Decimal("0.9"),
        )
        decision = await router.route(proposal)
        print(f"  {action.value:20s} → tier={decision.tier.value}")

        if decision.tier == ActionTier.STEER and decision.steering:
            print(f"    guidance: {decision.steering.guidance}")
            print(f"    suggested: {[str(a) for a in decision.steering.suggested_actions]}")
            print(f"    max_retries: {decision.steering.max_retries}")
        print()


# ── 3. MCP gateway: catch SteeringRequiredError ─────────────────


class EchoExecutor:
    def execute(self, context: ToolCallContext) -> ToolCallResult:
        return ToolCallResult(ok=True, output={"tool": context.tool_name}, cost=Decimal("0.5"))


def demo_mcp_gateway() -> None:
    print("=== 2. MCP gateway steering ===\n")

    db_path = Path("./steering_demo.db")
    db_path.unlink(missing_ok=True)

    cp = SyncControlPlane(f"sqlite:///{db_path}")
    cp.setup()

    gateway = McpGateway(
        cp,
        EchoExecutor(),
        ToolPolicyMap(
            {
                "get_status": ActionName.STATUS,
                "change_address": ActionName.CHANGE_ADDRESS,
                "reset_password": ActionName.RESET_PASSWORD,
            }
        ),
        config=McpGatewayConfig(policy_snapshot=POLICY),
    )

    sid = cp.create_session("steering-demo", max_cost=Decimal("100"), max_action_count=50)

    # Auto-approved tool call
    result = gateway.handle_tool_call(
        ToolCallContext(tool_name="get_status", session_id=sid, estimated_cost=Decimal("0.5"))
    )
    print(f"  get_status       → executed (ok={result.ok})")

    # Steered tool calls
    for tool in ["change_address", "reset_password"]:
        try:
            gateway.handle_tool_call(ToolCallContext(tool_name=tool, session_id=sid, estimated_cost=Decimal("1.0")))
        except SteeringRequiredError as exc:
            print(f"  {tool:18s} → steered")
            print(f"    reason: {exc.reason}")
            print(f"    guidance: {exc.steering.guidance}")
            print(f"    alternatives: {[str(a) for a in exc.steering.suggested_actions]}")

    events = cp.replay_events(sid)
    steered_events = [e for e in events if "steered" in (e.payload or {}).get("reason", "")]
    print(f"\n  Total events: {len(events)}, steered events: {len(steered_events)}")

    cp.close()
    db_path.unlink(missing_ok=True)


# ── Main ─────────────────────────────────────────────────────────


def main() -> None:
    asyncio.run(demo_routing())
    demo_mcp_gateway()
    print("\nDone.")


if __name__ == "__main__":
    main()
