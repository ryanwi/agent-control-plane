"""Crash recovery: detect and resume in-progress sessions on startup."""

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_control_plane.engine.event_store import EventStore
from agent_control_plane.engine.session_manager import SessionManager
from agent_control_plane.models.registry import ModelRegistry
from agent_control_plane.types.enums import AbortReason, EventKind, SessionStatus

logger = logging.getLogger(__name__)


class CrashRecovery:
    """Recovers control sessions that were interrupted by a process crash."""

    def __init__(
        self,
        session_manager: SessionManager,
        event_store: EventStore,
    ) -> None:
        self.session_manager = session_manager
        self.event_store = event_store

    async def recover_on_startup(self, db_session: AsyncSession) -> dict:
        """Scan for sessions with active cycles and attempt recovery.

        Called once on application startup.

        Returns summary of recovery actions taken.
        """
        ControlSession = ModelRegistry.get("ControlSession")
        result = await db_session.execute(
            select(ControlSession).where(
                ControlSession.status == SessionStatus.ACTIVE,
                ControlSession.active_cycle_id.is_not(None),
            )
        )
        stuck_sessions = list(result.scalars().all())

        recovered = 0
        aborted = 0

        for cs in stuck_sessions:
            try:
                await self._recover_session(db_session, cs)
                recovered += 1
            except Exception as e:
                logger.error("Failed to recover session %s: %s", cs.id, e)
                await self.session_manager.abort_session(
                    db_session,
                    cs.id,
                    AbortReason.SYSTEM_ERROR,
                    f"Crash recovery failed: {e}",
                )
                aborted += 1

        if stuck_sessions:
            await db_session.commit()
            logger.info(
                "Crash recovery: %d stuck sessions found, %d recovered, %d aborted",
                len(stuck_sessions),
                recovered,
                aborted,
            )

        return {
            "stuck_sessions": len(stuck_sessions),
            "recovered": recovered,
            "aborted": aborted,
        }

    async def _recover_session(self, db_session: AsyncSession, cs: Any) -> None:
        """Attempt to recover a single session.

        Strategy: Look at the last event to determine where the cycle was
        when the crash occurred. If the cycle completed analysis but
        hadn't finished, release the cycle lock so the next beat can start fresh.
        """
        last_event = await self._get_last_event(db_session, cs.id)

        if last_event is None:
            logger.info("Session %s: no events found, releasing cycle lock", cs.id)
            cs.active_cycle_id = None
            return

        logger.info(
            "Session %s: last event was %s (seq=%d), releasing cycle lock",
            cs.id,
            last_event.event_kind,
            last_event.seq,
        )

        # Emit a recovery event
        await self.event_store.append(
            db_session,
            session_id=cs.id,
            event_kind=EventKind.CYCLE_RECOVERED,
            payload={
                "last_event_kind": last_event.event_kind,
                "last_event_seq": last_event.seq,
                "recovered_cycle_id": str(cs.active_cycle_id),
            },
        )

        # Release the cycle lock so next beat can proceed
        cs.active_cycle_id = None

    async def _get_last_event(self, db_session: AsyncSession, session_id: UUID) -> Any | None:
        """Get the most recent event for a session."""
        ControlEvent = ModelRegistry.get("ControlEvent")
        result = await db_session.execute(
            select(ControlEvent).where(ControlEvent.session_id == session_id).order_by(ControlEvent.seq.desc()).limit(1)
        )
        return result.scalar_one_or_none()
