"""Concurrency enforcement for control plane operations."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from agent_control_plane.storage.protocols import AsyncProposalRepository, AsyncSessionRepository

logger = logging.getLogger(__name__)


class CycleAlreadyActiveError(Exception):
    """Raised when attempting to start a cycle while one is already active."""


class ResourceLockedError(Exception):
    """Raised when a proposal conflicts with a pending approval for the same resource."""


class ConcurrencyGuard:
    """Enforces one-active-cycle per session and resource-level proposal locking."""

    def __init__(
        self,
        session_repo: AsyncSessionRepository,
        proposal_repo: AsyncProposalRepository,
    ) -> None:
        self._session_repo = session_repo
        self._proposal_repo = proposal_repo

    async def acquire_cycle(self, session_id: UUID, cycle_id: UUID) -> None:
        """Attempt to start a new cycle. Raises if one is already active."""
        cs = await self._session_repo.get_session_for_update(session_id)
        if cs.active_cycle_id is not None:
            raise CycleAlreadyActiveError(f"Session {session_id} already has active cycle {cs.active_cycle_id}")
        await self._session_repo.set_active_cycle(session_id, cycle_id)

    async def release_cycle(self, session_id: UUID) -> None:
        """Release the active cycle lock."""
        await self._session_repo.set_active_cycle(session_id, None)

    async def check_resource_lock(self, session_id: UUID, resource_id: str) -> None:
        """Check if there's a pending approval for the same resource.

        Raises ResourceLockedError if a conflicting proposal exists.
        """
        if await self._proposal_repo.has_pending_for_resource(session_id, resource_id):
            raise ResourceLockedError(f"Pending proposal for {resource_id} blocks new proposals")
