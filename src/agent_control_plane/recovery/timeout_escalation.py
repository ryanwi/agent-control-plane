"""Detect stuck cycles and escalate with timeout enforcement."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from agent_control_plane.engine.event_store import EventStore
from agent_control_plane.engine.session_manager import SessionManager
from agent_control_plane.types.enums import EventKind, SessionStatus
from agent_control_plane.types.sessions import SessionState

if TYPE_CHECKING:
    from agent_control_plane.storage.protocols import AsyncEventRepository, AsyncSessionRepository

logger = logging.getLogger(__name__)

# Default cycle timeout: 15 minutes
DEFAULT_CYCLE_TIMEOUT_SECONDS = 900


class TimeoutEscalation:
    """Detects cycles that have exceeded their timeout and triggers escalation."""

    def __init__(
        self,
        session_manager: SessionManager,
        event_store: EventStore,
        session_repo: AsyncSessionRepository,
        event_repo: AsyncEventRepository,
        cycle_timeout_seconds: int = DEFAULT_CYCLE_TIMEOUT_SECONDS,
    ) -> None:
        self.session_manager = session_manager
        self.event_store = event_store
        self._session_repo = session_repo
        self._event_repo = event_repo
        self.cycle_timeout_seconds = cycle_timeout_seconds

    async def check_stuck_cycles(self) -> dict:
        """Check for active sessions with cycles that have exceeded timeout."""
        sessions = await self._session_repo.list_sessions(statuses=[SessionStatus.ACTIVE])
        active_sessions = [s for s in sessions if s.active_cycle_id is not None]

        escalated = 0
        now = datetime.now(UTC)
        timeout_threshold = now - timedelta(seconds=self.cycle_timeout_seconds)

        for cs in active_sessions:
            last_event = await self._event_repo.get_last_event(cs.id)
            if last_event is None:
                if cs.created_at and cs.created_at < timeout_threshold:
                    await self._escalate(cs, "No events found, session timed out")
                    escalated += 1
                continue

            if last_event.created_at < timeout_threshold:
                await self._escalate(
                    cs,
                    f"Last event ({last_event.event_kind}) at seq={last_event.seq} "
                    f"was {(now - last_event.created_at).total_seconds():.0f}s ago",
                )
                escalated += 1

        if escalated:
            logger.warning("Timeout escalation: %d stuck cycles aborted", escalated)

        return {"checked": len(active_sessions), "escalated": escalated}

    async def _escalate(self, cs: SessionState, details: str) -> None:
        """Abort a stuck cycle."""
        logger.warning("Escalating stuck cycle for session %s: %s", cs.id, details)

        cycle_id = cs.active_cycle_id
        await self._session_repo.set_active_cycle(cs.id, None)

        try:
            await self.event_store.append(
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
