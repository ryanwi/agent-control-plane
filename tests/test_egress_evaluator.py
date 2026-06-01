"""Tests for the egress capability-grant evaluator.

Encodes the blog lesson that an allowlist is a *capability grant*, not a destination
filter: reaching an allowlisted destination is necessary but not sufficient — the
specific capability exercised at that destination must also be granted. Allowing
api.anthropic.com for messages must NOT implicitly allow file uploads to it.
"""

from uuid import uuid4

import pytest

from agent_control_plane.evaluators import EgressEvaluator, EgressEvaluatorConfig, EgressGrant
from agent_control_plane.types.enums import ActionName, ExecutionMode
from agent_control_plane.types.policies import PolicySnapshot
from agent_control_plane.types.proposals import ActionProposal


def _policy() -> PolicySnapshot:
    return PolicySnapshot(execution_mode=ExecutionMode.DRY_RUN)


def _proposal(**overrides) -> ActionProposal:
    defaults = {
        "session_id": uuid4(),
        "resource_id": "api.anthropic.com",
        "resource_type": "egress",
        "decision": ActionName.STATUS,
        "reasoning": "test",
    }
    defaults.update(overrides)
    return ActionProposal(**defaults)


def _evaluator(**overrides) -> EgressEvaluator:
    config = {
        "grants": [EgressGrant(destination="api.anthropic.com", capabilities=[ActionName.STATUS.value])],
    }
    config.update(overrides)
    return EgressEvaluator(EgressEvaluatorConfig(**config))


class TestEgressEvaluator:
    @pytest.mark.asyncio
    async def test_allowed_destination_and_granted_capability_allows(self):
        result = await _evaluator().evaluate(_proposal(), _policy())
        assert result.allow

    @pytest.mark.asyncio
    async def test_allowed_destination_but_ungranted_capability_denies(self):
        # The Files API lesson: destination is allowlisted, but this capability is not granted.
        result = await _evaluator().evaluate(_proposal(decision=ActionName.WIRE_TRANSFER), _policy())
        assert not result.allow
        assert "capability" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_unknown_destination_denied_fail_closed(self):
        result = await _evaluator().evaluate(_proposal(resource_id="evil.example.com"), _policy())
        assert not result.allow
        assert "evil.example.com" in result.reason

    @pytest.mark.asyncio
    async def test_url_resource_id_host_is_extracted(self):
        result = await _evaluator().evaluate(_proposal(resource_id="https://api.anthropic.com/v1/messages"), _policy())
        assert result.allow

    @pytest.mark.asyncio
    async def test_protocol_relative_url_host_is_extracted(self):
        # A protocol-relative URL (//host/path) is a valid URL form and must resolve its host.
        result = await _evaluator().evaluate(_proposal(resource_id="//api.anthropic.com/v1/messages"), _policy())
        assert result.allow

    @pytest.mark.asyncio
    async def test_url_path_does_not_widen_capability(self):
        # A files upload URL on an allowlisted host is still denied when the capability isn't granted.
        result = await _evaluator().evaluate(
            _proposal(resource_id="https://api.anthropic.com/v1/files", decision=ActionName.WIRE_TRANSFER),
            _policy(),
        )
        assert not result.allow

    @pytest.mark.asyncio
    async def test_url_form_grant_destination_is_normalized_to_host(self):
        # A grant written in URL form must match host-form proposals (and vice versa).
        ev = EgressEvaluator(
            EgressEvaluatorConfig(
                grants=[
                    EgressGrant(
                        destination="https://api.anthropic.com/v1/messages",
                        capabilities=[ActionName.STATUS.value],
                    )
                ]
            )
        )
        host_form = await ev.evaluate(_proposal(resource_id="api.anthropic.com"), _policy())
        url_form = await ev.evaluate(_proposal(resource_id="https://api.anthropic.com/v1/messages"), _policy())
        assert host_form.allow
        assert url_form.allow

    @pytest.mark.asyncio
    async def test_subdomain_matches_when_enabled(self):
        result = await _evaluator().evaluate(_proposal(resource_id="eu.api.anthropic.com"), _policy())
        assert result.allow

    @pytest.mark.asyncio
    async def test_subdomain_not_matched_when_disabled(self):
        result = await _evaluator(match_subdomains=False).evaluate(
            _proposal(resource_id="eu.api.anthropic.com"), _policy()
        )
        assert not result.allow

    @pytest.mark.asyncio
    async def test_capability_field_is_configurable(self):
        ev = EgressEvaluator(
            EgressEvaluatorConfig(
                grants=[EgressGrant(destination="api.anthropic.com", capabilities=["read"])],
                capability_field="resource_type",
            )
        )
        allowed = await ev.evaluate(_proposal(resource_type="read"), _policy())
        denied = await ev.evaluate(_proposal(resource_type="write"), _policy())
        assert allowed.allow
        assert not denied.allow

    @pytest.mark.asyncio
    async def test_name_and_schema(self):
        ev = _evaluator()
        assert ev.name == "egress"
        assert ev.config_schema is EgressEvaluatorConfig
