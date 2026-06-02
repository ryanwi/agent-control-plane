"""Crash recovery: detect and resume in-progress sessions on startup."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from agent_control_plane.engine.event_store import EventStore
from agent_control_plane.engine.session_manager import SessionManager
from agent_control_plane.engine.state_integrity import (
    IntegrityViolation,
    SessionStateIntegrityError,
    validate_session_integrity,
)
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
        """Scan ACTIVE sessions on startup, recover stuck cycles, and validate persisted state.

        Stuck sessions (those still holding a cycle lock) are recovered; their state is also
        validated. Every other ACTIVE session is reloaded and trusted on startup just the same,
        so it is swept through the integrity check and aborted (fail closed) if its persisted
        state violates an invariant.

        Returns summary of recovery actions taken.
        """
        sessions = await self._session_repo.list_sessions(statuses=[SessionStatus.ACTIVE])
        # Partition up front: recovery mutates active_cycle_id, so classify before the loops run.
        stuck_sessions = [s for s in sessions if s.active_cycle_id is not None]
        non_stuck_sessions = [s for s in sessions if s.active_cycle_id is None]

        recovered = 0
        aborted = 0

        for cs in stuck_sessions:
            try:
                await self._recover_session(cs)
                recovered += 1
            except (RuntimeError, ValueError, SessionStateIntegrityError) as e:
                logger.error("Failed to recover session %s: %s", cs.id, e)
                await self.session_manager.abort_session(
                    cs.id,
                    AbortReason.SYSTEM_ERROR,
                    f"Crash recovery failed: {e}",
                )
                aborted += 1

        for cs in non_stuck_sessions:
            violations = validate_session_integrity(cs)
            if not violations:
                continue
            await self._emit_state_invalid(cs, violations)
            await self.session_manager.abort_session(
                cs.id,
                AbortReason.SYSTEM_ERROR,
                f"State integrity violations on startup: {', '.join(v.code for v in violations)}",
            )
            aborted += 1

        if stuck_sessions or aborted:
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

    async def _emit_state_invalid(self, cs: SessionState, violations: list[IntegrityViolation]) -> None:
        """Record a state-bearing SESSION_STATE_INVALID audit event for *cs*."""
        await self.event_store.append(
            session_id=cs.id,
            event_kind=EventKind.SESSION_STATE_INVALID,
            payload={"violations": [{"code": v.code, "message": v.message} for v in violations]},
            state_bearing=True,
        )

    async def _recover_session(self, cs: SessionState) -> None:
        """Attempt to recover a single session, aborting if state invariants are violated."""
        violations = validate_session_integrity(cs)
        if violations:
            await self._emit_state_invalid(cs, violations)
            raise SessionStateIntegrityError(violations)

        last_event = await self._event_repo.get_last_event(cs.id)

        if last_event is None:
            logger.info("Session %s: no events found, releasing cycle lock", cs.id)
            await self._session_repo.set_active_cycle(cs.id, None)
            return

        logger.info(
            "Session %s: last event was %s (seq=%d), releasing cycle lock",
            cs.id,
            last_event.kind,
            last_event.seq,
        )

        await self.event_store.append(
            session_id=cs.id,
            event_kind=EventKind.CYCLE_RECOVERED,
            payload={
                "last_event_kind": last_event.kind,
                "last_event_seq": last_event.seq,
                "recovered_cycle_id": str(cs.active_cycle_id),
            },
        )

        await self._session_repo.set_active_cycle(cs.id, None)
