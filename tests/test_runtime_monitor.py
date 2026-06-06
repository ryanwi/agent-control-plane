"""Tests for the RuntimeMonitor cooperative mid-execution interrupt protocol."""

from __future__ import annotations

import asyncio
import threading
import time
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from agent_control_plane.engine.event_store import EventStore
from agent_control_plane.engine.runtime_monitor import (
    CancellableExecution,
    RuntimeMonitor,
    RuntimeMonitorConfig,
)
from agent_control_plane.engine.session_risk_accumulator import SessionRiskAccumulator
from agent_control_plane.mcp import (
    McpGateway,
    McpGatewayConfig,
    ToolCallContext,
    ToolCallResult,
    ToolPolicyMap,
)
from agent_control_plane.sync import SyncControlPlane
from agent_control_plane.types.enums import EventKind, RiskLevel
from agent_control_plane.types.policies import ActionTiers, PolicySnapshot
from agent_control_plane.types.proposals import ActionProposal

from .fakes import InMemoryEventRepository


def _proposal(session_id: UUID, decision: str = "noop") -> ActionProposal:
    return ActionProposal(
        session_id=session_id,
        resource_id="res-1",
        resource_type="task",
        decision=decision,
        reasoning="test",
    )


class _FakeExecution:
    """A CancellableExecution whose state the test drives directly."""

    def __init__(self) -> None:
        self.cancelled = False
        self.cancel_reason: str | None = None
        self._running = True

    @property
    def is_running(self) -> bool:
        return self._running

    async def cancel(self, reason: str) -> None:
        self.cancelled = True
        self.cancel_reason = reason
        self._running = False

    def finish(self) -> None:
        self._running = False


def test_fake_execution_satisfies_protocol():
    assert isinstance(_FakeExecution(), CancellableExecution)


async def _accumulate(acc: SessionRiskAccumulator, sid: UUID, *, count: int, level: RiskLevel) -> None:
    for _ in range(count):
        await acc.assess(sid, _proposal(sid), level)


# ---------------------------------------------------------------------------
# Core monitor behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_monitor_fires_cancel_when_risk_escalates_midflight():
    repo = InMemoryEventRepository()
    store = EventStore(repo)
    acc = SessionRiskAccumulator()
    sid = uuid4()
    # Admitted at LOW with a single low action — below any threshold.
    await acc.assess(sid, _proposal(sid), RiskLevel.LOW)

    execution = _FakeExecution()
    monitor = RuntimeMonitor(
        execution,
        acc,
        event_store=store,
        config=RuntimeMonitorConfig(threshold=RiskLevel.HIGH, poll_interval_seconds=0.01),
    )

    async with monitor.watching(sid, _proposal(sid), RiskLevel.LOW):
        await asyncio.sleep(0.03)
        assert execution.cancelled is False  # nothing has escalated yet
        # A concurrent burst of high-risk actions pushes accumulated score past HIGH.
        await _accumulate(acc, sid, count=3, level=RiskLevel.HIGH)
        await asyncio.sleep(0.05)

    assert execution.cancelled is True
    assert execution.cancel_reason is not None


@pytest.mark.asyncio
async def test_monitor_does_not_fire_when_execution_completes_before_threshold():
    repo = InMemoryEventRepository()
    store = EventStore(repo)
    acc = SessionRiskAccumulator()
    sid = uuid4()
    await acc.assess(sid, _proposal(sid), RiskLevel.LOW)  # well below threshold

    execution = _FakeExecution()
    monitor = RuntimeMonitor(
        execution,
        acc,
        event_store=store,
        config=RuntimeMonitorConfig(threshold=RiskLevel.HIGH, poll_interval_seconds=0.01),
    )

    async with monitor.watching(sid, _proposal(sid), RiskLevel.LOW):
        execution.finish()  # completes naturally before any escalation
        await asyncio.sleep(0.05)

    assert execution.cancelled is False
    events = await repo.replay(sid)
    assert not any(e.kind == EventKind.RUNTIME_INTERRUPT_REQUESTED for e in events)


@pytest.mark.asyncio
async def test_runtime_interrupt_requested_event_in_audit_log_after_cancel():
    repo = InMemoryEventRepository()
    store = EventStore(repo)
    acc = SessionRiskAccumulator()
    sid = uuid4()
    # Pre-accumulate to HIGH so the first poll fires immediately.
    await _accumulate(acc, sid, count=10, level=RiskLevel.LOW)

    execution = _FakeExecution()
    monitor = RuntimeMonitor(
        execution,
        acc,
        event_store=store,
        config=RuntimeMonitorConfig(threshold=RiskLevel.HIGH, poll_interval_seconds=0.01),
    )

    async with monitor.watching(sid, _proposal(sid), RiskLevel.LOW):
        await asyncio.sleep(0.05)

    assert execution.cancelled is True
    events = await repo.replay(sid)
    interrupts = [e for e in events if e.kind == EventKind.RUNTIME_INTERRUPT_REQUESTED]
    assert len(interrupts) == 1
    assert interrupts[0].state_bearing is True
    assert interrupts[0].payload["escalated_risk"] == RiskLevel.HIGH.value
    assert interrupts[0].payload["admitted_risk"] == RiskLevel.LOW.value


@pytest.mark.asyncio
async def test_monitor_fires_only_once():
    repo = InMemoryEventRepository()
    store = EventStore(repo)
    acc = SessionRiskAccumulator()
    sid = uuid4()
    await _accumulate(acc, sid, count=10, level=RiskLevel.LOW)

    # An execution that keeps reporting running even after cancel, to prove the monitor
    # stops after a single interrupt rather than re-firing every poll.
    class _StubbornExecution:
        def __init__(self) -> None:
            self.cancel_calls = 0

        @property
        def is_running(self) -> bool:
            return True

        async def cancel(self, reason: str) -> None:
            self.cancel_calls += 1

    execution = _StubbornExecution()
    monitor = RuntimeMonitor(
        execution,
        acc,
        event_store=store,
        config=RuntimeMonitorConfig(threshold=RiskLevel.HIGH, poll_interval_seconds=0.01),
    )

    async with monitor.watching(sid, _proposal(sid), RiskLevel.LOW):
        await asyncio.sleep(0.05)

    assert execution.cancel_calls == 1
    events = await repo.replay(sid)
    assert sum(1 for e in events if e.kind == EventKind.RUNTIME_INTERRUPT_REQUESTED) == 1


@pytest.mark.asyncio
async def test_state_bearing_audit_failure_raises():
    # Fail-closed: a persistence failure for the state-bearing interrupt event must
    # surface, not be swallowed.
    repo = InMemoryEventRepository(fail=True)
    store = EventStore(repo)
    acc = SessionRiskAccumulator()
    sid = uuid4()
    await _accumulate(acc, sid, count=10, level=RiskLevel.LOW)

    execution = _FakeExecution()
    monitor = RuntimeMonitor(
        execution,
        acc,
        event_store=store,
        config=RuntimeMonitorConfig(threshold=RiskLevel.HIGH, poll_interval_seconds=0.01),
    )

    with pytest.raises(RuntimeError):
        async with monitor.watching(sid, _proposal(sid), RiskLevel.LOW):
            await asyncio.sleep(0.05)

    # The audit write failed before the cancel was issued (record-then-cancel).
    assert execution.cancelled is False


@pytest.mark.asyncio
async def test_monitor_without_event_store_still_cancels():
    acc = SessionRiskAccumulator()
    sid = uuid4()
    await _accumulate(acc, sid, count=10, level=RiskLevel.LOW)

    execution = _FakeExecution()
    monitor = RuntimeMonitor(
        execution,
        acc,
        config=RuntimeMonitorConfig(threshold=RiskLevel.HIGH, poll_interval_seconds=0.01),
    )

    async with monitor.watching(sid, _proposal(sid), RiskLevel.LOW):
        await asyncio.sleep(0.05)

    assert execution.cancelled is True


@pytest.mark.asyncio
async def test_stop_is_idempotent_and_safe_without_watch():
    acc = SessionRiskAccumulator()
    monitor = RuntimeMonitor(_FakeExecution(), acc)
    await monitor.stop()  # no watch started — no-op
    await monitor.stop()


# ---------------------------------------------------------------------------
# McpGateway wiring
# ---------------------------------------------------------------------------


class _BlockingExecutor:
    """Sync tool executor that spins until cancelled (or a safety timeout elapses)."""

    def __init__(self) -> None:
        self.stop = threading.Event()
        self.running = False
        self.observed_cancel = False

    def execute(self, context: ToolCallContext) -> ToolCallResult:
        self.running = True
        try:
            for _ in range(200):  # ~2s safety cap
                if self.stop.is_set():
                    self.observed_cancel = True
                    break
                time.sleep(0.01)
            return ToolCallResult(ok=True, output={"tool": context.tool_name}, cost=Decimal("1.0"))
        finally:
            self.running = False


class _ExecutionHandle:
    def __init__(self, executor: _BlockingExecutor) -> None:
        self._executor = executor

    @property
    def is_running(self) -> bool:
        return self._executor.running

    async def cancel(self, reason: str) -> None:
        self._executor.stop.set()


class _QuickExecutor:
    def execute(self, context: ToolCallContext) -> ToolCallResult:
        return ToolCallResult(ok=True, output={"tool": context.tool_name}, cost=Decimal("1.0"))

    # Doubles as its own (never-running) execution handle.
    @property
    def is_running(self) -> bool:
        return False

    async def cancel(self, reason: str) -> None:  # pragma: no cover - never called
        raise AssertionError("cancel should not be called when nothing escalates")


def _auto_status_gateway(cp: SyncControlPlane, executor, acc, *, execution_factory) -> McpGateway:
    policy = PolicySnapshot(action_tiers=ActionTiers(auto_approve=["status"]))
    config = McpGatewayConfig(
        policy_snapshot=policy,
        execution_factory=execution_factory,
        runtime_monitor_config=RuntimeMonitorConfig(threshold=RiskLevel.HIGH, poll_interval_seconds=0.01),
    )
    return McpGateway(
        cp,
        executor,
        ToolPolicyMap({"status": "status"}),
        config=config,
        risk_accumulator=acc,
    )


def test_gateway_cancels_execution_when_session_risk_escalates(tmp_path: Path):
    db_file = tmp_path / "monitor_fire.db"
    cp = SyncControlPlane(f"sqlite:///{db_file}")
    cp.setup()
    sid = cp.create_session("mcp-monitor", max_cost=Decimal("50"), max_action_count=50)

    acc = SessionRiskAccumulator()
    # Pre-accumulate session risk to HIGH before the in-flight call begins.
    for _ in range(10):
        asyncio.run(acc.assess(sid, _proposal(sid), RiskLevel.LOW))

    executor = _BlockingExecutor()
    gateway = _auto_status_gateway(cp, executor, acc, execution_factory=lambda ctx: _ExecutionHandle(executor))

    result = gateway.handle_tool_call(
        ToolCallContext(tool_name="status", session_id=sid, estimated_cost=Decimal("1.0"))
    )

    assert result.ok is True
    assert executor.observed_cancel is True  # the cooperative signal reached the executor
    events = cp.replay_events(sid)
    assert EventKind.RUNTIME_INTERRUPT_REQUESTED in [e.kind for e in events]
    cp.close()


def test_gateway_completes_normally_without_escalation(tmp_path: Path):
    db_file = tmp_path / "monitor_nofire.db"
    cp = SyncControlPlane(f"sqlite:///{db_file}")
    cp.setup()
    sid = cp.create_session("mcp-monitor-quiet", max_cost=Decimal("50"), max_action_count=50)

    acc = SessionRiskAccumulator()  # empty — no accumulated risk
    executor = _QuickExecutor()
    gateway = _auto_status_gateway(cp, executor, acc, execution_factory=lambda ctx: executor)

    result = gateway.handle_tool_call(
        ToolCallContext(tool_name="status", session_id=sid, estimated_cost=Decimal("1.0"))
    )

    assert result.ok is True
    events = cp.replay_events(sid)
    assert EventKind.RUNTIME_INTERRUPT_REQUESTED not in [e.kind for e in events]
    assert EventKind.EXECUTION_COMPLETED in [e.kind for e in events]
    cp.close()
