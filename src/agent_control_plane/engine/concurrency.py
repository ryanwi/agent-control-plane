"""Concurrency enforcement for control plane operations."""

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_control_plane.models.registry import ModelRegistry
from agent_control_plane.types.enums import ProposalStatus

logger = logging.getLogger(__name__)


class CycleAlreadyActiveError(Exception):
    """Raised when attempting to start a cycle while one is already active."""


class ResourceLockedError(Exception):
    """Raised when a proposal conflicts with a pending approval for the same resource."""


class ConcurrencyGuard:
    """Enforces one-active-cycle per session and resource-level proposal locking."""

    async def acquire_cycle(self, session: AsyncSession, session_id: UUID, cycle_id: UUID) -> None:
        """Attempt to start a new cycle. Raises if one is already active.

        Uses SELECT ... FOR UPDATE to prevent race conditions.
        """
        ControlSession = ModelRegistry.get("ControlSession")
        result = await session.execute(select(ControlSession).where(ControlSession.id == session_id).with_for_update())
        cs = result.scalar_one()

        if cs.active_cycle_id is not None:
            raise CycleAlreadyActiveError(f"Session {session_id} already has active cycle {cs.active_cycle_id}")

        cs.active_cycle_id = cycle_id
        await session.flush()

    async def release_cycle(self, session: AsyncSession, session_id: UUID) -> None:
        """Release the active cycle lock."""
        ControlSession = ModelRegistry.get("ControlSession")
        result = await session.execute(select(ControlSession).where(ControlSession.id == session_id).with_for_update())
        cs = result.scalar_one()
        cs.active_cycle_id = None
        await session.flush()

    async def check_resource_lock(
        self,
        session: AsyncSession,
        session_id: UUID,
        resource_id: str,
    ) -> None:
        """Check if there's a pending approval for the same resource.

        Raises ResourceLockedError if a conflicting proposal exists.
        """
        ActionProposal = ModelRegistry.get("ActionProposal")

        result = await session.execute(
            select(ActionProposal)
            .where(
                ActionProposal.session_id == session_id,
                ActionProposal.resource_id == resource_id,
                ActionProposal.status == ProposalStatus.PENDING,
            )
            .limit(1)
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            raise ResourceLockedError(f"Pending proposal {existing.id} for {resource_id} blocks new proposals")
