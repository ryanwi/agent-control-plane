"""Atomic budget tracking per control session."""

import logging
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agent_control_plane.models.registry import ModelRegistry

logger = logging.getLogger(__name__)


class BudgetExhaustedError(Exception):
    """Raised when a session's budget is exhausted."""


class BudgetTracker:
    """Atomic notional/count budget management per session."""

    async def check_budget(
        self,
        session: AsyncSession,
        session_id: UUID,
        notional_amount: Decimal = Decimal("0"),
        action_count: int = 1,
    ) -> bool:
        """Check if the proposed action fits within session budget.

        Returns True if within budget, False otherwise.
        """
        cs = await self._get_session(session, session_id)
        remaining_notional = cs.max_notional - cs.used_notional
        remaining_count = cs.max_action_count - cs.used_action_count
        return notional_amount <= remaining_notional and action_count <= remaining_count

    async def increment(
        self,
        session: AsyncSession,
        session_id: UUID,
        notional_amount: Decimal,
        action_count: int = 1,
    ) -> None:
        """Atomically increment used budget within a transaction.

        Raises BudgetExhaustedError if the increment would exceed limits.
        """
        ControlSession = ModelRegistry.get("ControlSession")
        # Lock the row for atomic update
        result = await session.execute(
            select(ControlSession).where(ControlSession.id == session_id).with_for_update()
        )
        cs = result.scalar_one()

        new_notional = cs.used_notional + notional_amount
        new_count = cs.used_action_count + action_count

        if new_notional > cs.max_notional:
            raise BudgetExhaustedError(
                f"Notional budget exceeded: {new_notional} > {cs.max_notional}"
            )
        if new_count > cs.max_action_count:
            raise BudgetExhaustedError(
                f"Action count budget exceeded: {new_count} > {cs.max_action_count}"
            )

        await session.execute(
            update(ControlSession)
            .where(ControlSession.id == session_id)
            .values(used_notional=new_notional, used_action_count=new_count)
        )
        logger.debug(
            "Budget updated for session %s: notional=%s/%s, count=%d/%d",
            session_id,
            new_notional,
            cs.max_notional,
            new_count,
            cs.max_action_count,
        )

    async def get_remaining(self, session: AsyncSession, session_id: UUID) -> dict:
        """Get remaining budget for a session."""
        cs = await self._get_session(session, session_id)
        return {
            "remaining_notional": cs.max_notional - cs.used_notional,
            "remaining_count": cs.max_action_count - cs.used_action_count,
            "used_notional": cs.used_notional,
            "used_count": cs.used_action_count,
            "max_notional": cs.max_notional,
            "max_count": cs.max_action_count,
        }

    async def _get_session(self, session: AsyncSession, session_id: UUID) -> object:
        ControlSession = ModelRegistry.get("ControlSession")
        result = await session.execute(
            select(ControlSession).where(ControlSession.id == session_id)
        )
        cs = result.scalar_one_or_none()
        if cs is None:
            raise ValueError(f"Session {session_id} not found")
        return cs
