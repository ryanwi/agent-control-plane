"""Cooperative mid-execution interrupt protocol.

ACP governs proposals *between* submissions: once an action clears the kill switch
and starts running, the control plane is blind to it until the data plane reports an
outcome. ``RuntimeMonitor`` closes that gap without owning the executor. It watches a
session's accumulated risk while an action is in flight and, when risk escalates past a
configurable threshold, *asks* the host executor to cancel via the
:class:`CancellableExecution` protocol.

The contract is cooperative, not coercive: ACP signals and records the request; whether
execution actually stops is the executor's responsibility. The monitor never intercepts
the executor's result or swallows its errors — it observes and signals alongside.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable
from uuid import UUID

from agent_control_plane.types.enums import EventKind, RiskLevel

if TYPE_CHECKING:
    from agent_control_plane.engine.session_risk_accumulator import SessionRiskAccumulator
    from agent_control_plane.types.frames import EventMetadata
    from agent_control_plane.types.proposals import ActionProposal
    from agent_control_plane.types.risk import SessionRiskEscalation

logger = logging.getLogger(__name__)

_DEFAULT_POLL_INTERVAL_SECONDS = 0.25


@runtime_checkable
class CancellableExecution(Protocol):
    """Host-executor handle that ACP can cooperatively ask to stop.

    Host applications opt in by implementing this protocol; nothing in ACP requires it.
    The executor decides what "cancel" means — cancelling an :mod:`asyncio` task, setting
    a :class:`threading.Event`, writing a stop file, signalling a subprocess, etc.
    """

    async def cancel(self, reason: str) -> None:
        """Request that the in-flight execution stop. Best-effort and cooperative."""

    @property
    def is_running(self) -> bool:
        """Whether the execution is still in progress.

        ACP checks this before signalling so it can skip a cancel for work that already
        completed naturally.
        """


@runtime_checkable
class RuntimeEventSink(Protocol):
    """Audit sink for the interrupt request — satisfied by :class:`EventStore`.

    Typed as a protocol so synchronous hosts (e.g. ``McpGateway``) can bridge the event
    to their own sync persistence path while async hosts pass an ``EventStore`` directly.
    """

    async def append(
        self,
        session_id: UUID,
        event_kind: EventKind,
        payload: dict[str, Any],
        *,
        state_bearing: bool = False,
        metadata: EventMetadata | None = None,
    ) -> int | None: ...


@dataclass(frozen=True)
class RuntimeMonitorConfig:
    """Tuning for :class:`RuntimeMonitor`.

    ``threshold`` is the escalated risk level at (or above) which an interrupt is
    requested. ``poll_interval_seconds`` is how often the accumulator is re-checked while
    an execution is in flight.
    """

    threshold: RiskLevel = RiskLevel.HIGH
    poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS


class RuntimeMonitor:
    """Watches session risk during a single in-flight execution and signals interrupts.

    The accumulator has no subscription surface, so the monitor polls it with the
    non-mutating :meth:`SessionRiskAccumulator.peek`. When the peeked risk escalates above
    the admitted level *and* reaches the configured threshold, the monitor records a
    state-bearing ``RUNTIME_INTERRUPT_REQUESTED`` event and asks the execution to cancel —
    exactly once per watch.

    Typical use wraps the execution in :meth:`watching`::

        monitor = RuntimeMonitor(execution, accumulator, event_store=store)
        async with monitor.watching(session_id, proposal, admitted_risk):
            result = await run_the_action()
    """

    def __init__(
        self,
        execution: CancellableExecution,
        accumulator: SessionRiskAccumulator,
        *,
        event_store: RuntimeEventSink | None = None,
        config: RuntimeMonitorConfig | None = None,
    ) -> None:
        self._execution = execution
        self._accumulator = accumulator
        self._event_store = event_store
        self._config = config or RuntimeMonitorConfig()
        self._task: asyncio.Task[None] | None = None

    @asynccontextmanager
    async def watching(
        self,
        session_id: UUID,
        proposal: ActionProposal,
        action_risk_level: RiskLevel,
    ) -> AsyncIterator[RuntimeMonitor]:
        """Async context manager: start watching on enter, stop on exit."""
        await self.watch(session_id, proposal, action_risk_level)
        try:
            yield self
        finally:
            await self.stop()

    async def watch(
        self,
        session_id: UUID,
        proposal: ActionProposal,
        action_risk_level: RiskLevel,
    ) -> None:
        """Start the background poll loop. Idempotent while a watch is active."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(session_id, proposal, action_risk_level))

    async def stop(self) -> None:
        """Stop watching and propagate any error raised inside the poll loop.

        A pending loop is cancelled; a loop that already fired (or failed while recording
        the state-bearing interrupt event) is awaited so its outcome surfaces here rather
        than being silently dropped.
        """
        task = self._task
        if task is None:
            return
        self._task = None
        if not task.done():
            task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _run(
        self,
        session_id: UUID,
        proposal: ActionProposal,
        action_risk_level: RiskLevel,
    ) -> None:
        while self._execution.is_running:
            escalation = self._accumulator.peek(session_id, action_risk_level)
            if self._should_interrupt(escalation):
                await self._request_interrupt(session_id, proposal, action_risk_level, escalation)
                return
            await asyncio.sleep(self._config.poll_interval_seconds)

    def _should_interrupt(self, escalation: SessionRiskEscalation) -> bool:
        # ``was_escalated`` already means the effective risk rose above the admitted
        # level — a genuine mid-flight spike. The threshold gates how high it must rise.
        return escalation.was_escalated and escalation.escalated_risk.rank >= self._config.threshold.rank

    async def _request_interrupt(
        self,
        session_id: UUID,
        proposal: ActionProposal,
        action_risk_level: RiskLevel,
        escalation: SessionRiskEscalation,
    ) -> None:
        # The execution may have finished between the peek and now — don't ask a completed
        # run to stop, and don't record an interrupt we never sent.
        if not self._execution.is_running:
            return
        reason = (
            f"session risk escalated to {escalation.escalated_risk.value} "
            f"(>= threshold {self._config.threshold.value}) mid-execution"
        )
        # Record first: the audit event is state-bearing and fail-closed, so a persistence
        # failure raises and prevents a cancel that ACP could not record asking for.
        await self._record_interrupt(session_id, proposal, action_risk_level, escalation, reason=reason)
        logger.warning(
            "RuntimeMonitor requesting cancel for session %s proposal %s: %s",
            session_id,
            proposal.id,
            reason,
        )
        await self._execution.cancel(reason)

    async def _record_interrupt(
        self,
        session_id: UUID,
        proposal: ActionProposal,
        action_risk_level: RiskLevel,
        escalation: SessionRiskEscalation,
        *,
        reason: str,
    ) -> None:
        if self._event_store is None:
            return
        await self._event_store.append(
            session_id,
            EventKind.RUNTIME_INTERRUPT_REQUESTED,
            {
                "session_id": str(session_id),
                "proposal_id": str(proposal.id),
                "decision": str(proposal.decision),
                "admitted_risk": action_risk_level.value,
                "escalated_risk": escalation.escalated_risk.value,
                "threshold": self._config.threshold.value,
                "reason": reason,
                "escalation_reasons": escalation.escalation_reasons,
            },
            state_bearing=True,
        )
