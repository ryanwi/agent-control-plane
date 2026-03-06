"""Agent registry and delegation governance."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from agent_control_plane.types.agents import AgentMetadata, DelegationProposal

if TYPE_CHECKING:
    from agent_control_plane.storage.protocols import AsyncAgentRepository

logger = logging.getLogger(__name__)


class AgentRegistry:
    """Central registry for agent identities and capabilities."""

    def __init__(self, repo: AsyncAgentRepository) -> None:
        self._repo = repo

    async def register(self, agent: AgentMetadata) -> None:
        """Register or update an agent's metadata and capabilities."""
        await self._repo.register_agent(agent)
        logger.info("Registered agent %s (%s)", agent.name, agent.id)

    async def get_agent(self, agent_id: str) -> AgentMetadata | None:
        """Retrieve agent metadata by ID."""
        return await self._repo.get_agent(agent_id)

    async def list_agents(self, tags: list[str] | None = None) -> list[AgentMetadata]:
        """List registered agents, optionally filtered by tags."""
        return await self._repo.list_agents(tags=tags)


class DelegationGuard:
    """Governs delegation of tasks between agents."""

    def __init__(self, agent_registry: AgentRegistry, repo: AsyncAgentRepository) -> None:
        self.registry = agent_registry
        self._repo = repo

    async def propose_delegation(self, proposal: DelegationProposal) -> bool:
        """Check if a delegation request is allowed and record it.

        Rules:
        1. Both source and target agents must exist in the registry.
        2. (Future) Apply policy-based delegation rules.
        """
        source = await self.registry.get_agent(proposal.source_agent_id)
        target = await self.registry.get_agent(proposal.target_agent_id)

        if not source:
            logger.warning("Delegation failed: source agent %s not found", proposal.source_agent_id)
            return False
        if not target:
            logger.warning("Delegation failed: target agent %s not found", proposal.target_agent_id)
            return False

        # Record the delegation attempt for audit
        await self._repo.record_delegation(proposal)
        logger.info(
            "Delegation proposed: %s -> %s (Task: %s)",
            proposal.source_agent_id,
            proposal.target_agent_id,
            proposal.task_description,
        )
        return True
