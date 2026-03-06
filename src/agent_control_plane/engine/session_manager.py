"""Control session lifecycle management."""

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agent_control_plane.models.registry import ModelRegistry
from agent_control_plane.types.enums import AbortReason, SessionStatus

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages ControlSession CRUD and lifecycle transitions."""

    async def create_session(
        self,
        session: AsyncSession,
        *,
        session_name: str,
        execution_mode: str = "dry_run",
        asset_scope: str | None = None,
        max_notional: Decimal = Decimal("100000"),
        max_action_count: int = 50,
        policy_id: UUID | None = None,
        dry_run_session_id: UUID | None = None,
    ) -> Any:
        """Create a new control session with its sequence counter."""
        ControlSession = ModelRegistry.get("ControlSession")
        SessionSeqCounter = ModelRegistry.get("SessionSeqCounter")

        cs = ControlSession(
            id=uuid4(),
            session_name=session_name,
            status=SessionStatus.CREATED,
            execution_mode=execution_mode,
            asset_scope=asset_scope,
            max_notional=max_notional,
            max_action_count=max_action_count,
            active_policy_id=policy_id,
            dry_run_session_id=dry_run_session_id,
        )
        session.add(cs)
        await session.flush()

        # Create the sequence counter for this session
        counter = SessionSeqCounter(id=uuid4(), session_id=cs.id, next_seq=1)
        session.add(counter)
        await session.flush()

        logger.info("Created control session %s (%s)", session_name, cs.id)
        return cs

    async def get_session(self, session: AsyncSession, session_id: UUID) -> Any | None:
        """Get a session by ID."""
        ControlSession = ModelRegistry.get("ControlSession")
        result = await session.execute(select(ControlSession).where(ControlSession.id == session_id))
        return result.scalar_one_or_none()

    async def get_session_by_name(self, session: AsyncSession, session_name: str) -> Any | None:
        """Get a session by name."""
        ControlSession = ModelRegistry.get("ControlSession")
        result = await session.execute(select(ControlSession).where(ControlSession.session_name == session_name))
        return result.scalar_one_or_none()

    async def list_sessions(
        self,
        session: AsyncSession,
        status: str | None = None,
        limit: int = 50,
    ) -> list[Any]:
        """List sessions, optionally filtered by status."""
        ControlSession = ModelRegistry.get("ControlSession")
        query = select(ControlSession).order_by(ControlSession.created_at.desc()).limit(limit)
        if status:
            query = query.where(ControlSession.status == status)
        result = await session.execute(query)
        return list(result.scalars().all())

    async def activate_session(self, session: AsyncSession, session_id: UUID) -> Any:
        """Transition session from CREATED to ACTIVE."""
        cs = await self._get_session_or_raise(session, session_id)
        if cs.status != SessionStatus.CREATED:
            raise ValueError(f"Cannot activate session in state {cs.status}")
        cs.status = SessionStatus.ACTIVE
        cs.updated_at = datetime.now(UTC)
        await session.flush()
        return cs

    async def pause_session(self, session: AsyncSession, session_id: UUID) -> Any:
        """Pause an active session."""
        cs = await self._get_session_or_raise(session, session_id)
        if cs.status != SessionStatus.ACTIVE:
            raise ValueError(f"Cannot pause session in state {cs.status}")
        cs.status = SessionStatus.PAUSED
        cs.updated_at = datetime.now(UTC)
        await session.flush()
        return cs

    async def resume_session(self, session: AsyncSession, session_id: UUID) -> Any:
        """Resume a paused session."""
        cs = await self._get_session_or_raise(session, session_id)
        if cs.status != SessionStatus.PAUSED:
            raise ValueError(f"Cannot resume session in state {cs.status}")
        cs.status = SessionStatus.ACTIVE
        cs.updated_at = datetime.now(UTC)
        await session.flush()
        return cs

    async def complete_session(self, session: AsyncSession, session_id: UUID) -> Any:
        """Mark a session as completed."""
        cs = await self._get_session_or_raise(session, session_id)
        if cs.status not in (SessionStatus.ACTIVE, SessionStatus.PAUSED):
            raise ValueError(f"Cannot complete session in state {cs.status}")
        cs.status = SessionStatus.COMPLETED
        cs.active_cycle_id = None
        cs.updated_at = datetime.now(UTC)
        await session.flush()
        return cs

    async def abort_session(
        self,
        session: AsyncSession,
        session_id: UUID,
        reason: AbortReason,
        details: str | None = None,
    ) -> Any:
        """Abort a session with reason."""
        cs = await self._get_session_or_raise(session, session_id)
        if cs.status in (SessionStatus.COMPLETED, SessionStatus.ABORTED):
            raise ValueError(f"Cannot abort session in state {cs.status}")
        cs.status = SessionStatus.ABORTED
        cs.abort_reason = reason
        cs.abort_details = details
        cs.active_cycle_id = None
        cs.updated_at = datetime.now(UTC)
        await session.flush()
        logger.warning("Aborted session %s: %s", session_id, reason)
        return cs

    async def set_active_cycle(self, session: AsyncSession, session_id: UUID, cycle_id: UUID | None) -> None:
        """Set or clear the active cycle for a session."""
        ControlSession = ModelRegistry.get("ControlSession")
        await session.execute(
            update(ControlSession)
            .where(ControlSession.id == session_id)
            .values(active_cycle_id=cycle_id, updated_at=datetime.now(UTC))
        )

    async def has_active_cycle(self, session: AsyncSession, session_id: UUID) -> bool:
        """Check if a session has an active cycle in progress."""
        cs = await self._get_session_or_raise(session, session_id)
        return cs.active_cycle_id is not None

    async def create_policy(
        self,
        session: AsyncSession,
        *,
        action_tiers: dict,
        risk_limits: dict,
        asset_scope: str | None = None,
        execution_mode: str = "dry_run",
        approval_timeout_seconds: int = 3600,
        auto_approve_conditions: dict,
    ) -> Any:
        """Create an immutable policy snapshot."""
        PolicySnapshot = ModelRegistry.get("PolicySnapshot")
        policy = PolicySnapshot(
            id=uuid4(),
            action_tiers=action_tiers,
            risk_limits=risk_limits,
            asset_scope=asset_scope,
            execution_mode=execution_mode,
            approval_timeout_seconds=approval_timeout_seconds,
            auto_approve_conditions=auto_approve_conditions,
        )
        session.add(policy)
        await session.flush()
        return policy

    async def _get_session_or_raise(self, session: AsyncSession, session_id: UUID) -> Any:
        """Get session or raise ValueError."""
        cs = await self.get_session(session, session_id)
        if cs is None:
            raise ValueError(f"Session {session_id} not found")
        return cs
