"""Control session lifecycle management."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from agent_control_plane.types.enums import AbortReason, SessionStatus
from agent_control_plane.types.sessions import SessionState

if TYPE_CHECKING:
    from agent_control_plane.storage.protocols import AsyncSessionRepository

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages ControlSession CRUD and lifecycle transitions."""

    def __init__(self, session_repo: AsyncSessionRepository) -> None:
        self._repo = session_repo

    async def create_session(
        self,
        *,
        session_name: str,
        execution_mode: str = "dry_run",
        asset_scope: str | None = None,
        max_cost: Decimal = Decimal("100000"),
        max_action_count: int = 50,
        policy_id: UUID | None = None,
        dry_run_session_id: UUID | None = None,
    ) -> SessionState:
        """Create a new control session with its sequence counter."""
        cs = await self._repo.create_session(
            session_name=session_name,
            status=SessionStatus.CREATED,
            execution_mode=execution_mode,
            asset_scope=asset_scope,
            max_cost=max_cost,
            max_action_count=max_action_count,
            active_policy_id=policy_id,
            dry_run_session_id=dry_run_session_id,
        )
        await self._repo.create_seq_counter(cs.id)
        logger.info("Created control session %s (%s)", session_name, cs.id)
        return cs

    async def get_session(self, session_id: UUID) -> SessionState | None:
        """Get a session by ID."""
        return await self._repo.get_session(session_id)

    async def list_sessions(
        self,
        status: str | None = None,
        limit: int = 50,
    ) -> list[SessionState]:
        """List sessions, optionally filtered by status."""
        statuses = [status] if status else None
        return await self._repo.list_sessions(statuses=statuses, limit=limit)

    async def activate_session(self, session_id: UUID) -> SessionState:
        """Transition session from CREATED to ACTIVE."""
        cs = await self._get_session_or_raise(session_id)
        if cs.status != SessionStatus.CREATED:
            raise ValueError(f"Cannot activate session in state {cs.status}")
        await self._repo.update_session(session_id, status=SessionStatus.ACTIVE, updated_at=datetime.now(UTC))
        cs.status = SessionStatus.ACTIVE
        return cs

    async def pause_session(self, session_id: UUID) -> SessionState:
        """Pause an active session."""
        cs = await self._get_session_or_raise(session_id)
        if cs.status != SessionStatus.ACTIVE:
            raise ValueError(f"Cannot pause session in state {cs.status}")
        await self._repo.update_session(session_id, status=SessionStatus.PAUSED, updated_at=datetime.now(UTC))
        cs.status = SessionStatus.PAUSED
        return cs

    async def resume_session(self, session_id: UUID) -> SessionState:
        """Resume a paused session."""
        cs = await self._get_session_or_raise(session_id)
        if cs.status != SessionStatus.PAUSED:
            raise ValueError(f"Cannot resume session in state {cs.status}")
        await self._repo.update_session(session_id, status=SessionStatus.ACTIVE, updated_at=datetime.now(UTC))
        cs.status = SessionStatus.ACTIVE
        return cs

    async def complete_session(self, session_id: UUID) -> SessionState:
        """Mark a session as completed."""
        cs = await self._get_session_or_raise(session_id)
        if cs.status not in (SessionStatus.ACTIVE, SessionStatus.PAUSED):
            raise ValueError(f"Cannot complete session in state {cs.status}")
        await self._repo.update_session(
            session_id,
            status=SessionStatus.COMPLETED,
            active_cycle_id=None,
            updated_at=datetime.now(UTC),
        )
        cs.status = SessionStatus.COMPLETED
        return cs

    async def abort_session(
        self,
        session_id: UUID,
        reason: AbortReason,
        details: str | None = None,
    ) -> SessionState:
        """Abort a session with reason."""
        cs = await self._get_session_or_raise(session_id)
        if cs.status in (SessionStatus.COMPLETED, SessionStatus.ABORTED):
            raise ValueError(f"Cannot abort session in state {cs.status}")
        await self._repo.update_session(
            session_id,
            status=SessionStatus.ABORTED,
            abort_reason=reason,
            abort_details=details,
            active_cycle_id=None,
            updated_at=datetime.now(UTC),
        )
        cs.status = SessionStatus.ABORTED
        logger.warning("Aborted session %s: %s", session_id, reason)
        return cs

    async def set_active_cycle(self, session_id: UUID, cycle_id: UUID | None) -> None:
        """Set or clear the active cycle for a session."""
        await self._repo.set_active_cycle(session_id, cycle_id)

    async def has_active_cycle(self, session_id: UUID) -> bool:
        """Check if a session has an active cycle in progress."""
        cs = await self._get_session_or_raise(session_id)
        return cs.active_cycle_id is not None

    async def create_policy(self, **kwargs: Any) -> Any:
        """Create an immutable policy snapshot. Returns the policy ID."""
        policy_id = await self._repo.create_policy(**kwargs)

        # Return a simple namespace with .id for backwards compat
        class _PolicyRef:
            def __init__(self, id: UUID):
                self.id = id

        return _PolicyRef(policy_id)

    async def _get_session_or_raise(self, session_id: UUID) -> SessionState:
        """Get session or raise ValueError."""
        cs = await self._repo.get_session(session_id)
        if cs is None:
            raise ValueError(f"Session {session_id} not found")
        return cs
