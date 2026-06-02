"""Tests for MCP tool-call governance gateway."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from agent_control_plane.mcp import (
    ApprovalRequiredError,
    McpGateway,
    McpGatewayConfig,
    PolicyDeniedError,
    ToolCallContext,
    ToolCallResult,
    ToolExecutionError,
    ToolPolicyMap,
)
from agent_control_plane.sync import SyncControlPlane
from agent_control_plane.telemetry import export_event
from agent_control_plane.types.enums import ActionName, EventKind, GovernanceOutcome
from agent_control_plane.types.policies import ActionTiers, PolicySnapshot


class _OkExecutor:
    def execute(self, context: ToolCallContext) -> ToolCallResult:
        return ToolCallResult(ok=True, output={"tool": context.tool_name}, cost=Decimal("1.25"))


class _FailingExecutor:
    def execute(self, context: ToolCallContext) -> ToolCallResult:
        return ToolCallResult(ok=False, output={}, error="boom")


class _Tracer:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def add_event(self, name: str, attributes: dict[str, object]) -> None:
        self.events.append((name, attributes))


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
    assert [e.kind for e in events] == [EventKind.CYCLE_STARTED, EventKind.APPROVAL_DENIED]
    cp.close()


def test_manual_approval_creates_ticket_and_blocks(tmp_path: Path):
    cp = _new_cp(tmp_path, "mcp_approval")
    sid = cp.create_session("mcp-approval")

    policy = PolicySnapshot(action_tiers=ActionTiers(always_approve=[ActionName.REFUND]))
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
    assert EventKind.APPROVAL_REQUESTED in [e.kind for e in events]
    cp.close()


def test_auto_approved_tool_executes_and_consumes_budget(tmp_path: Path):
    cp = _new_cp(tmp_path, "mcp_execute")
    sid = cp.create_session("mcp-execute", max_cost=Decimal("5"), max_action_count=5)

    policy = PolicySnapshot(action_tiers=ActionTiers(auto_approve=[ActionName.STATUS]))
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
    assert EventKind.EXECUTION_COMPLETED in [e.kind for e in events]
    cp.close()


def test_correlation_id_is_propagated_to_emitted_events(tmp_path: Path):
    cp = _new_cp(tmp_path, "mcp_corr")
    sid = cp.create_session("mcp-corr")

    policy = PolicySnapshot(action_tiers=ActionTiers(auto_approve=[ActionName.STATUS]))
    gateway = McpGateway(
        cp,
        _OkExecutor(),
        ToolPolicyMap({"status": ActionName.STATUS}),
        config=McpGatewayConfig(policy_snapshot=policy),
    )

    corr = uuid4()
    gateway.handle_tool_call(ToolCallContext(tool_name="status", session_id=sid, correlation_id=corr))

    events = cp.replay_events(sid)
    assert any(e.correlation_id == corr for e in events), "correlation_id must reach emitted events"
    cp.close()


def test_revoked_agent_is_blocked(tmp_path: Path):
    cp = _new_cp(tmp_path, "mcp_revoked")
    sid = cp.create_session("mcp-revoked")

    policy = PolicySnapshot(action_tiers=ActionTiers(auto_approve=[ActionName.STATUS]))
    gateway = McpGateway(
        cp,
        _OkExecutor(),
        ToolPolicyMap({"status": ActionName.STATUS}),
        config=McpGatewayConfig(policy_snapshot=policy),
    )

    # Revoke the agent for this session.
    with cp.session_scope() as db:
        uow = cp.uow_factory(db)
        uow.agent_repo.record_revocation(sid, "agent-x", "suspected compromise")
        uow.commit()

    with pytest.raises(PolicyDeniedError):
        gateway.handle_tool_call(ToolCallContext(tool_name="status", session_id=sid, agent_id="agent-x"))

    # A different, non-revoked agent is unaffected.
    result = gateway.handle_tool_call(ToolCallContext(tool_name="status", session_id=sid, agent_id="agent-y"))
    assert result.ok is True
    cp.close()


def test_no_session_id_raises_by_default(tmp_path: Path):
    """McpGatewayConfig.auto_create_sessions must default to False."""
    cp = _new_cp(tmp_path, "mcp_no_session")
    policy = PolicySnapshot(action_tiers=ActionTiers(auto_approve=[ActionName.STATUS]))
    gateway = McpGateway(
        cp,
        _OkExecutor(),
        ToolPolicyMap({"status": ActionName.STATUS}),
        config=McpGatewayConfig(policy_snapshot=policy),
    )
    with pytest.raises(PolicyDeniedError):
        gateway.handle_tool_call(ToolCallContext(tool_name="status"))  # no session_id


def test_auto_create_sessions_opt_in_still_works(tmp_path: Path):
    """Hosts that explicitly enable auto_create_sessions still get sessions created."""
    cp = _new_cp(tmp_path, "mcp_autocreate")
    policy = PolicySnapshot(action_tiers=ActionTiers(auto_approve=[ActionName.STATUS]))
    gateway = McpGateway(
        cp,
        _OkExecutor(),
        ToolPolicyMap({"status": ActionName.STATUS}),
        config=McpGatewayConfig(policy_snapshot=policy, auto_create_sessions=True),
    )
    result = gateway.handle_tool_call(ToolCallContext(tool_name="status"))
    assert result.ok is True
    cp.close()


def test_failed_tool_call_does_not_export_as_applied(tmp_path: Path):
    cp = _new_cp(tmp_path, "mcp_fail_outcome")
    sid = cp.create_session("mcp-fail-outcome")

    policy = PolicySnapshot(action_tiers=ActionTiers(auto_approve=[ActionName.STATUS]))
    gateway = McpGateway(
        cp,
        _FailingExecutor(),
        ToolPolicyMap({"status": ActionName.STATUS}),
        config=McpGatewayConfig(policy_snapshot=policy),
    )

    with pytest.raises(ToolExecutionError):
        gateway.handle_tool_call(ToolCallContext(tool_name="status", session_id=sid))

    failed = [e for e in cp.replay_events(sid) if e.payload.get("error") is not None]
    assert failed, "expected a TOOL_CALL_FAILED event"

    tracer = _Tracer()
    export_event(failed[-1], tracer=tracer)
    _, attrs = tracer.events[0]
    # A tool failure must not be exported as a successful application.
    assert attrs.get("cp.outcome") == GovernanceOutcome.FAILED.value
    assert attrs.get("cp.outcome") != GovernanceOutcome.APPLIED.value
    cp.close()
