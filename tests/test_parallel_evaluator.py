"""Tests for cancel-on-deny parallel evaluation."""

import asyncio

import pytest

from agent_control_plane.engine.parallel_evaluator import ParallelPolicyEvaluator
from agent_control_plane.evaluators.protocol import EvaluatorResult


def _allow(reason: str = "ok"):
    async def _fn() -> EvaluatorResult:
        return EvaluatorResult(allow=True, reason=reason)

    return _fn


def _deny(reason: str = "denied"):
    async def _fn() -> EvaluatorResult:
        return EvaluatorResult(allow=False, reason=reason)

    return _fn


def _slow_allow(delay: float = 0.1, reason: str = "slow ok"):
    async def _fn() -> EvaluatorResult:
        await asyncio.sleep(delay)
        return EvaluatorResult(allow=True, reason=reason)

    return _fn


def _error():
    async def _fn() -> EvaluatorResult:
        raise RuntimeError("boom")

    return _fn


class TestParallelPolicyEvaluator:
    @pytest.mark.asyncio
    async def test_all_allow(self):
        pe = ParallelPolicyEvaluator(max_concurrent=3)
        result = await pe.evaluate_all([_allow("a"), _allow("b"), _allow("c")])
        assert result.overall_allow is True
        assert len(result.results) == 3
        assert result.cancelled_count == 0

    @pytest.mark.asyncio
    async def test_first_deny_cancels_remaining(self):
        pe = ParallelPolicyEvaluator(max_concurrent=1)
        result = await pe.evaluate_all([_deny(), _slow_allow(0.5), _slow_allow(0.5)])
        assert result.overall_allow is False
        # The deny should have set the event, slow tasks should be cancelled
        assert result.cancelled_count > 0

    @pytest.mark.asyncio
    async def test_empty_evaluators_returns_allow(self):
        pe = ParallelPolicyEvaluator()
        result = await pe.evaluate_all([])
        assert result.overall_allow is True
        assert result.results == []
        assert result.cancelled_count == 0

    @pytest.mark.asyncio
    async def test_exception_treated_as_deny(self):
        pe = ParallelPolicyEvaluator()
        result = await pe.evaluate_all([_allow(), _error()])
        assert result.overall_allow is False
        deny_results = [r for r in result.results if not r.allow]
        assert len(deny_results) == 1
        assert "error" in deny_results[0].reason.lower()

    @pytest.mark.asyncio
    async def test_elapsed_ms_populated(self):
        pe = ParallelPolicyEvaluator()
        result = await pe.evaluate_all([_allow()])
        assert result.elapsed_ms >= 0

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self):
        """With max_concurrent=1, tasks run sequentially."""
        execution_order: list[int] = []

        def _tracked(idx: int):
            async def _fn() -> EvaluatorResult:
                execution_order.append(idx)
                await asyncio.sleep(0.01)
                return EvaluatorResult(allow=True, reason=f"task-{idx}")

            return _fn

        pe = ParallelPolicyEvaluator(max_concurrent=1)
        result = await pe.evaluate_all([_tracked(0), _tracked(1), _tracked(2)])
        assert result.overall_allow is True
        assert len(result.results) == 3

    @pytest.mark.asyncio
    async def test_integration_with_condition_evaluator(self):
        """Verify ParallelPolicyEvaluator works with evaluators from registry."""
        from uuid import uuid4

        from agent_control_plane.evaluators import (
            EvaluatorRegistry,
            ListEvaluator,
            ListEvaluatorConfig,
            RegexEvaluator,
            RegexEvaluatorConfig,
        )
        from agent_control_plane.types.enums import ActionName, ExecutionMode
        from agent_control_plane.types.policies import PolicySnapshot
        from agent_control_plane.types.proposals import ActionProposal

        registry = EvaluatorRegistry(auto_discover=False)
        regex_ev = RegexEvaluator(RegexEvaluatorConfig(patterns=[r"^safe"], field="resource_id", deny_on_match=False))
        list_ev = ListEvaluator(ListEvaluatorConfig(allowlist=["status"], field="decision"))
        registry.register(regex_ev)
        registry.register(list_ev)

        policy = PolicySnapshot(execution_mode=ExecutionMode.DRY_RUN)
        proposal = ActionProposal(
            session_id=uuid4(),
            resource_id="safe-001",
            resource_type="task",
            decision=ActionName.STATUS,
            reasoning="test",
        )

        evaluator_fns = [lambda ev=ev: ev.evaluate(proposal, policy) for ev in registry.all()]

        pe = ParallelPolicyEvaluator(max_concurrent=2)
        result = await pe.evaluate_all(evaluator_fns)
        assert result.overall_allow is True
        assert len(result.results) == 2
