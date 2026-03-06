"""Detect stuck cycles and escalate with timeout enforcement."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_control_plane.engine.event_store import EventStore
from agent_control_plane.engine.session_manager import SessionManager
from agent_control_plane.models.registry import ModelRegistry
from agent_control_plane.types.enums import EventKind, SessionStatus

logger = logging.getLogger(__name__)

# Default cycle timeout: 15 minutes
DEFAULT_CYCLE_TIMEOUT_SECONDS = 900


class TimeoutEscalation:
    """Detects cycles that have exceeded their timeout and triggers escalation."""

    def __init__(
        self,
        session_manager: SessionManager,
        event_store: EventStore,
        cycle_timeout_seconds: int = DEFAULT_CYCLE_TIMEOUT_SECONDS,
    ) -> None:
        self.session_manager = session_manager
        self.event_store = event_store
        self.cycle_timeout_seconds = cycle_timeout_seconds

    async def check_stuck_cycles(self, db_session: AsyncSession) -> dict:
        """Check for active sessions with cycles that have exceeded timeout."""
        ControlSession = ModelRegistry.get("ControlSession")
        result = await db_session.execute(
            select(ControlSession).where(
                ControlSession.status == SessionStatus.ACTIVE,
                ControlSession.active_cycle_id.is_not(None),
            )
        )
        active_sessions = list(result.scalars().all())

        escalated = 0
        now = datetime.now(UTC)
        timeout_threshold = now - timedelta(seconds=self.cycle_timeout_seconds)

        for cs in active_sessions:
            last_event = await self._get_last_event(db_session, cs.id)
            if last_event is None:
                if cs.created_at and cs.created_at < timeout_threshold:
                    await self._escalate(db_session, cs, "No events found, session timed out")
                    escalated += 1
                continue

            if last_event.created_at < timeout_threshold:
                await self._escalate(
                    db_session,
                    cs,
                    f"Last event ({last_event.event_kind}) at seq={last_event.seq} "
                    f"was {(now - last_event.created_at).total_seconds():.0f}s ago",
                )
                escalated += 1

        if escalated:
            await db_session.commit()
            logger.warning("Timeout escalation: %d stuck cycles aborted", escalated)

        return {"checked": len(active_sessions), "escalated": escalated}

    async def _escalate(self, db_session: AsyncSession, cs: Any, details: str) -> None:
        """Abort a stuck cycle."""
        logger.warning("Escalating stuck cycle for session %s: %s", cs.id, details)

        # Release the cycle lock (don't abort the session, just the cycle)
        cycle_id = cs.active_cycle_id
        cs.active_cycle_id = None

        try:
            await self.event_store.append(
                db_session,
                session_id=cs.id,
                event_kind=EventKind.KILL_SWITCH_TRIGGERED,
                payload={
                    "scope": "agent_timeout",
                    "cycle_id": str(cycle_id) if cycle_id else None,
                    "details": details,
                },
                state_bearing=False,
            )
        except Exception:
            logger.exception("Failed to append timeout escalation event for session %s", cs.id)

    async def _get_last_event(self, db_session: AsyncSession, session_id: UUID) -> Any | None:
        """Get the most recent event for a session."""
        ControlEvent = ModelRegistry.get("ControlEvent")
        result = await db_session.execute(
            select(ControlEvent).where(ControlEvent.session_id == session_id).order_by(ControlEvent.seq.desc()).limit(1)
        )
        return result.scalar_one_or_none()
