"""Minimal MCP tool-call gateway example.

Run:
    uv run python examples/mcp_tool_gateway.py
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from agent_control_plane.mcp import (
    McpGateway,
    McpGatewayConfig,
    ToolCallContext,
    ToolCallResult,
    ToolPolicyMap,
)
from agent_control_plane.sync import SyncControlPlane
from agent_control_plane.types.enums import ActionName
from agent_control_plane.types.policies import ActionTiers, PolicySnapshotDTO


class DemoExecutor:
    def execute(self, context: ToolCallContext) -> ToolCallResult:
        return ToolCallResult(ok=True, output={"echo_tool": context.tool_name}, cost=Decimal("0.75"))


def main() -> None:
    Path("./control_plane_mcp_example.db").unlink(missing_ok=True)
    cp = SyncControlPlane("sqlite:///./control_plane_mcp_example.db")
    cp.setup()

    policy = PolicySnapshotDTO(action_tiers=ActionTiers(auto_approve=[ActionName.STATUS]))
    gateway = McpGateway(
        cp,
        DemoExecutor(),
        ToolPolicyMap({"status": ActionName.STATUS}),
        config=McpGatewayConfig(policy_snapshot=policy),
    )

    sid = cp.create_session("mcp-demo", max_cost=Decimal("10"), max_action_count=10)
    result = gateway.handle_tool_call(
        ToolCallContext(
            tool_name="status",
            session_id=sid,
            arguments={"resource_id": "svc-1"},
            estimated_cost=Decimal("0.50"),
        )
    )

    print("Result:", result.model_dump())
    print("Remaining budget:", cp.get_remaining_budget(sid))
    print("Events recorded:", len(cp.replay_events(sid)))
    cp.close()


if __name__ == "__main__":
    main()
