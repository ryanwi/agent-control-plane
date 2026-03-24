"""Tests for evaluator plugins: protocol, registry, and built-in evaluators."""

from uuid import uuid4

import pytest

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


def _policy() -> PolicySnapshot:
    return PolicySnapshot(
        action_tiers={"blocked": [], "always_approve": [], "auto_approve": [], "unrestricted": []},
        execution_mode=ExecutionMode.DRY_RUN,
    )


def _proposal(**overrides) -> ActionProposal:
    defaults = {
        "session_id": uuid4(),
        "resource_id": "res-001",
        "resource_type": "task",
        "decision": ActionName.STATUS,
        "reasoning": "test",
    }
    defaults.update(overrides)
    return ActionProposal(**defaults)


class TestRegexEvaluator:
    @pytest.mark.asyncio
    async def test_deny_on_match(self):
        ev = RegexEvaluator(RegexEvaluatorConfig(patterns=[r"res-\d+"], field="resource_id", deny_on_match=True))
        result = await ev.evaluate(_proposal(), _policy())
        assert not result.allow
        assert "res-001" in result.reason

    @pytest.mark.asyncio
    async def test_allow_when_no_match(self):
        ev = RegexEvaluator(RegexEvaluatorConfig(patterns=[r"^xyz"], field="resource_id", deny_on_match=True))
        result = await ev.evaluate(_proposal(), _policy())
        assert result.allow

    @pytest.mark.asyncio
    async def test_deny_on_no_match_when_inverted(self):
        ev = RegexEvaluator(RegexEvaluatorConfig(patterns=[r"^xyz"], field="resource_id", deny_on_match=False))
        result = await ev.evaluate(_proposal(), _policy())
        assert not result.allow

    @pytest.mark.asyncio
    async def test_name_and_schema(self):
        ev = RegexEvaluator(RegexEvaluatorConfig(patterns=[]))
        assert ev.name == "regex"
        assert ev.config_schema is RegexEvaluatorConfig


class TestListEvaluator:
    @pytest.mark.asyncio
    async def test_blocklist_denies(self):
        ev = ListEvaluator(ListEvaluatorConfig(blocklist=["status"], field="decision"))
        result = await ev.evaluate(_proposal(), _policy())
        assert not result.allow

    @pytest.mark.asyncio
    async def test_blocklist_priority_over_allowlist(self):
        ev = ListEvaluator(ListEvaluatorConfig(allowlist=["status"], blocklist=["status"], field="decision"))
        result = await ev.evaluate(_proposal(), _policy())
        assert not result.allow

    @pytest.mark.asyncio
    async def test_allowlist_pass(self):
        ev = ListEvaluator(ListEvaluatorConfig(allowlist=["status"], field="decision"))
        result = await ev.evaluate(_proposal(), _policy())
        assert result.allow

    @pytest.mark.asyncio
    async def test_allowlist_deny_when_not_in_list(self):
        ev = ListEvaluator(ListEvaluatorConfig(allowlist=["refund"], field="decision"))
        result = await ev.evaluate(_proposal(), _policy())
        assert not result.allow

    @pytest.mark.asyncio
    async def test_name_and_schema(self):
        ev = ListEvaluator(ListEvaluatorConfig())
        assert ev.name == "list"
        assert ev.config_schema is ListEvaluatorConfig


class TestEvaluatorRegistry:
    def test_manual_register_and_get(self):
        registry = EvaluatorRegistry(auto_discover=False)
        ev = RegexEvaluator(RegexEvaluatorConfig(patterns=[]))
        registry.register(ev)
        assert registry.get("regex") is ev

    def test_duplicate_name_raises(self):
        registry = EvaluatorRegistry(auto_discover=False)
        ev1 = RegexEvaluator(RegexEvaluatorConfig(patterns=[]))
        ev2 = RegexEvaluator(RegexEvaluatorConfig(patterns=["x"]))
        registry.register(ev1)
        with pytest.raises(ValueError, match="already registered"):
            registry.register(ev2)

    def test_get_unknown_returns_none(self):
        registry = EvaluatorRegistry(auto_discover=False)
        assert registry.get("nonexistent") is None

    def test_all_returns_registered(self):
        registry = EvaluatorRegistry(auto_discover=False)
        ev1 = RegexEvaluator(RegexEvaluatorConfig(patterns=[]))
        ev2 = ListEvaluator(ListEvaluatorConfig())
        registry.register(ev1)
        registry.register(ev2)
        assert len(registry.all()) == 2

    def test_auto_discover_no_entrypoints(self):
        registry = EvaluatorRegistry(auto_discover=True)
        # No crash, just empty (no entry points installed in test env)
        assert isinstance(registry.all(), list)
