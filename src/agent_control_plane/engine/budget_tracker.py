"""Atomic budget tracking per control session."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from agent_control_plane.types.sessions import BudgetInfo

if TYPE_CHECKING:
    from agent_control_plane.storage.protocols import AsyncSessionRepository

logger = logging.getLogger(__name__)


class BudgetExhaustedError(Exception):
    """Raised when a session's budget is exhausted."""


class BudgetTracker:
    """Atomic cost/count budget management per session."""

    def __init__(self, session_repo: AsyncSessionRepository) -> None:
        self._repo = session_repo

    async def check_budget(
        self,
        session_id: UUID,
        cost: Decimal = Decimal("0"),
        action_count: int = 1,
    ) -> bool:
        """Check if the proposed action fits within session budget.

        Returns True if within budget, False otherwise.
        """
        info = await self._repo.get_budget(session_id)
        return cost <= info.remaining_cost and action_count <= info.remaining_count

    async def increment(
        self,
        session_id: UUID,
        cost: Decimal,
        action_count: int = 1,
    ) -> None:
        """Atomically increment used budget.

        Raises BudgetExhaustedError if the increment would exceed limits.
        """
        await self._repo.increment_budget(session_id, cost, action_count)

    async def get_remaining(self, session_id: UUID) -> BudgetInfo:
        """Get remaining budget for a session."""
        return await self._repo.get_budget(session_id)
