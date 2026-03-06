"""Tests for MCP tool-call governance gateway."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from agent_control_plane.mcp import (
    ApprovalRequiredError,
    McpGateway,
    McpGatewayConfig,
    PolicyDeniedError,
    ToolCallContext,
    ToolCallResult,
    ToolPolicyMap,
)
from agent_control_plane.sync import SyncControlPlane
from agent_control_plane.types.enums import ActionName, EventKind
from agent_control_plane.types.policies import ActionTiers, PolicySnapshotDTO


class _OkExecutor:
    def execute(self, context: ToolCallContext) -> ToolCallResult:
        return ToolCallResult(ok=True, output={"tool": context.tool_name}, cost=Decimal("1.25"))


def _new_cp(tmp_path: Path, suffix: str) -> SyncControlPlane:
    db_file = tmp_path / f"{suffix}.db"
    cp = SyncControlPlane(f"sqlite:///{db_file}")
    cp.setup()
    return cp


def test_unknown_tool_fails_closed(tmp_path: Path):
    cp = _new_cp(tmp_path, "mcp_unknown")
    sid = cp.create_session("mcp-unknown")
    gateway = McpGateway(cp, _OkExecutor(), ToolPolicyMap({}))

    with pytest.raises(PolicyDeniedError):
        gateway.handle_tool_call(ToolCallContext(tool_name="dangerous_tool", session_id=sid))

    events = cp.replay_events(sid)
    assert [e.event_kind for e in events] == [EventKind.CYCLE_STARTED, EventKind.APPROVAL_DENIED]
    cp.close()


def test_manual_approval_creates_ticket_and_blocks(tmp_path: Path):
    cp = _new_cp(tmp_path, "mcp_approval")
    sid = cp.create_session("mcp-approval")

    policy = PolicySnapshotDTO(action_tiers=ActionTiers(always_approve=[ActionName.REFUND]))
    gateway = McpGateway(
        cp,
        _OkExecutor(),
        ToolPolicyMap({"issue_refund": ActionName.REFUND}),
        config=McpGatewayConfig(policy_snapshot=policy),
    )

    with pytest.raises(ApprovalRequiredError) as err:
        gateway.handle_tool_call(ToolCallContext(tool_name="issue_refund", session_id=sid))

    assert isinstance(err.value.ticket_id, UUID)
    events = cp.replay_events(sid)
    assert EventKind.APPROVAL_REQUESTED in [e.event_kind for e in events]
    cp.close()


def test_auto_approved_tool_executes_and_consumes_budget(tmp_path: Path):
    cp = _new_cp(tmp_path, "mcp_execute")
    sid = cp.create_session("mcp-execute", max_cost=Decimal("5"), max_action_count=5)

    policy = PolicySnapshotDTO(action_tiers=ActionTiers(auto_approve=[ActionName.STATUS]))
    gateway = McpGateway(
        cp,
        _OkExecutor(),
        ToolPolicyMap({"status": ActionName.STATUS}),
        config=McpGatewayConfig(policy_snapshot=policy),
    )

    result = gateway.handle_tool_call(
        ToolCallContext(tool_name="status", session_id=sid, estimated_cost=Decimal("1.00"))
    )
    assert result.ok is True

    budget = cp.get_remaining_budget(sid)
    assert budget["used_cost"] == Decimal("1.25")
    events = cp.replay_events(sid)
    assert EventKind.EXECUTION_COMPLETED in [e.event_kind for e in events]
    cp.close()
