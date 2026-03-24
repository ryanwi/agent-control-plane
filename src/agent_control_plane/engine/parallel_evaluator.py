"""Parallel policy evaluation with cancel-on-deny semantics."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from pydantic import BaseModel, Field

from agent_control_plane.evaluators.protocol import EvaluatorResult

logger = logging.getLogger(__name__)


class ParallelEvaluationResult(BaseModel):
    """Aggregated result of parallel evaluator execution."""

    overall_allow: bool
    results: list[EvaluatorResult] = Field(default_factory=list)
    cancelled_count: int = 0
    elapsed_ms: float = 0.0


class ParallelPolicyEvaluator:
    """Evaluate multiple policy evaluators concurrently with cancel-on-deny."""

    def __init__(self, *, max_concurrent: int = 3) -> None:
        self._max_concurrent = max_concurrent

    async def evaluate_all(
        self,
        evaluators: list[Callable[[], Awaitable[EvaluatorResult]]],
    ) -> ParallelEvaluationResult:
        """Run evaluators in parallel. Cancel remaining on first deny (fail-closed)."""
        if not evaluators:
            return ParallelEvaluationResult(overall_allow=True)

        start = time.monotonic()
        sem = asyncio.Semaphore(self._max_concurrent)
        deny_event = asyncio.Event()
        results: list[EvaluatorResult] = []
        cancelled = 0

        async def _run(fn: Callable[[], Awaitable[EvaluatorResult]]) -> EvaluatorResult | None:
            if deny_event.is_set():
                return None
            async with sem:
                if deny_event.is_set():
                    return None
                try:
                    result = await fn()
                except Exception as exc:
                    logger.warning("Evaluator raised exception (fail-closed): %s", exc)
                    result = EvaluatorResult(allow=False, reason=f"Evaluator error: {exc}")
                if not result.allow:
                    deny_event.set()
                return result

        tasks = [asyncio.create_task(_run(fn)) for fn in evaluators]

        for task in asyncio.as_completed(tasks):
            result = await task
            if result is not None:
                results.append(result)

        # Count tasks that returned None (cancelled/skipped)
        cancelled = len(evaluators) - len(results)

        elapsed = (time.monotonic() - start) * 1000
        overall = all(r.allow for r in results) if results else True

        return ParallelEvaluationResult(
            overall_allow=overall,
            results=results,
            cancelled_count=cancelled,
            elapsed_ms=elapsed,
        )
