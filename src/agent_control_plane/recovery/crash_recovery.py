"""Crash recovery: detect and resume in-progress sessions on startup."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from agent_control_plane.engine.event_store import EventStore
from agent_control_plane.engine.session_manager import SessionManager
from agent_control_plane.types.enums import AbortReason, EventKind, SessionStatus
from agent_control_plane.types.sessions import SessionState

if TYPE_CHECKING:
    from agent_control_plane.storage.protocols import AsyncEventRepository, AsyncSessionRepository

logger = logging.getLogger(__name__)


class CrashRecovery:
    """Recovers control sessions that were interrupted by a process crash."""

    def __init__(
        self,
        session_manager: SessionManager,
        event_store: EventStore,
        session_repo: AsyncSessionRepository,
        event_repo: AsyncEventRepository,
    ) -> None:
        self.session_manager = session_manager
        self.event_store = event_store
        self._session_repo = session_repo
        self._event_repo = event_repo

    async def recover_on_startup(self) -> dict[str, int]:
        """Scan for sessions with active cycles and attempt recovery.

        Called once on application startup.

        Returns summary of recovery actions taken.
        """
        sessions = await self._session_repo.list_sessions(statuses=[SessionStatus.ACTIVE])
        stuck_sessions = [s for s in sessions if s.active_cycle_id is not None]

        recovered = 0
        aborted = 0

        for cs in stuck_sessions:
            try:
                await self._recover_session(cs)
                recovered += 1
            except (RuntimeError, ValueError) as e:
                logger.error("Failed to recover session %s: %s", cs.id, e)
                await self.session_manager.abort_session(
                    cs.id,
                    AbortReason.SYSTEM_ERROR,
                    f"Crash recovery failed: {e}",
                )
                aborted += 1

        if stuck_sessions:
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

    async def _recover_session(self, cs: SessionState) -> None:
        """Attempt to recover a single session."""
        last_event = await self._event_repo.get_last_event(cs.id)

        if last_event is None:
            logger.info("Session %s: no events found, releasing cycle lock", cs.id)
            await self._session_repo.set_active_cycle(cs.id, None)
            return

        logger.info(
            "Session %s: last event was %s (seq=%d), releasing cycle lock",
            cs.id,
            last_event.event_kind,
            last_event.seq,
        )

        await self.event_store.append(
            session_id=cs.id,
            event_kind=EventKind.CYCLE_RECOVERED,
            payload={
                "last_event_kind": last_event.event_kind,
                "last_event_seq": last_event.seq,
                "recovered_cycle_id": str(cs.active_cycle_id),
            },
        )

        await self._session_repo.set_active_cycle(cs.id, None)
