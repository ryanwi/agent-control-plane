"""Agent registry and delegation governance."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID

from agent_control_plane.types.agents import AgentMetadata, DelegationProposal
from agent_control_plane.types.enums import EventKind
from agent_control_plane.types.ids import AgentId

if TYPE_CHECKING:
    from agent_control_plane.engine.event_store import EventStore
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
        return await self._repo.get_agent(AgentId(agent_id))

    async def list_agents(self, tags: list[str] | None = None) -> list[AgentMetadata]:
        """List registered agents, optionally filtered by tags."""
        return await self._repo.list_agents(tags=tags)


class AgentSessionGuard:
    """Per-session revocation of an agent's authority.

    Revokes one agent within one session — independently of its global registration and
    without aborting the whole session — and reports that status for fail-closed enforcement
    at authorization time. The fine-grained complement to deregister (global) and the kill
    switch (whole-session). Each (de)revocation is recorded and emits a state-bearing event.
    """

    def __init__(self, repo: AsyncAgentRepository, event_store: EventStore) -> None:
        self._repo = repo
        self._event_store = event_store

    async def revoke(self, session_id: UUID, agent_id: str, *, reason: str = "") -> None:
        """Revoke an agent's authority for ``session_id`` (fail-closed at authorization)."""
        await self._repo.record_revocation(session_id, agent_id, reason)
        await self._event_store.append(
            session_id=session_id,
            event_kind=EventKind.AGENT_REVOKED,
            payload={"agent_id": agent_id, "reason": reason},
            state_bearing=True,
        )
        logger.info("Agent %s revoked for session %s: %s", agent_id, session_id, reason)

    async def reinstate(self, session_id: UUID, agent_id: str) -> None:
        """Clear a prior revocation, restoring the agent's authority for ``session_id``."""
        await self._repo.clear_revocation(session_id, agent_id)
        await self._event_store.append(
            session_id=session_id,
            event_kind=EventKind.AGENT_REINSTATED,
            payload={"agent_id": agent_id},
            state_bearing=True,
        )
        logger.info("Agent %s reinstated for session %s", agent_id, session_id)

    async def is_revoked(self, session_id: UUID, agent_id: str) -> bool:
        """Whether ``agent_id`` is currently revoked for ``session_id``."""
        return await self._repo.is_agent_revoked(session_id, agent_id)


class DelegationGuard:
    """Governs delegation of tasks between agents."""

    def __init__(self, agent_registry: AgentRegistry, repo: AsyncAgentRepository) -> None:
        self.registry = agent_registry
        self._repo = repo

    async def propose_delegation(self, proposal: DelegationProposal) -> bool:
        """Check if a delegation request is allowed and record it.

        Delegation is an advisory/audit record: it does NOT elevate trust. The target agent
        is always authorized against its own registered capabilities (see
        ``AgentMetadata.is_capable``); it never inherits the source's authority. Do not treat
        a recorded delegation as a capability grant.

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
