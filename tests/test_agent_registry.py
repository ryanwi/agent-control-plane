"""Tests for AgentRegistry and DelegationGuard."""

import pytest

from agent_control_plane.engine.agent_registry import AgentRegistry, DelegationGuard
from agent_control_plane.types.agents import AgentCapability, AgentMetadata, DelegationProposal
from agent_control_plane.types.enums import ActionName

from .fakes import InMemoryAgentRepository


@pytest.fixture
def agent_repo():
    return InMemoryAgentRepository()


@pytest.fixture
def registry(agent_repo):
    return AgentRegistry(agent_repo)


@pytest.fixture
def guard(registry, agent_repo):
    return DelegationGuard(registry, agent_repo)


class TestAgentRegistry:
    @pytest.mark.asyncio
    async def test_register_and_get_agent(self, registry):
        agent = AgentMetadata(id="agent-1", name="Test Agent", capabilities=[AgentCapability(action=ActionName.STATUS)])
        await registry.register(agent)

        retrieved = await registry.get_agent("agent-1")
        assert retrieved.name == "Test Agent"
        assert retrieved.capabilities[0].action == ActionName.STATUS

    @pytest.mark.asyncio
    async def test_list_agents_by_tags(self, registry):
        await registry.register(AgentMetadata(id="a1", name="A1", tags=["worker"]))
        await registry.register(AgentMetadata(id="a2", name="A2", tags=["manager"]))

        workers = await registry.list_agents(tags=["worker"])
        assert len(workers) == 1
        assert workers[0].id == "a1"


class TestDelegationGuard:
    @pytest.mark.asyncio
    async def test_propose_delegation_success(self, registry, guard, agent_repo):
        await registry.register(AgentMetadata(id="src", name="Source"))
        await registry.register(AgentMetadata(id="target", name="Target"))

        proposal = DelegationProposal(source_agent_id="src", target_agent_id="target", task_description="Do work")

        allowed = await guard.propose_delegation(proposal)
        assert allowed is True
        assert len(agent_repo._delegations) == 1

    @pytest.mark.asyncio
    async def test_propose_delegation_fails_for_unknown_agent(self, registry, guard):
        await registry.register(AgentMetadata(id="src", name="Source"))

        proposal = DelegationProposal(source_agent_id="src", target_agent_id="unknown", task_description="Do work")

        allowed = await guard.propose_delegation(proposal)
        assert allowed is False
