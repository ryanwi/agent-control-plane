"""Kill switch mechanics for emergency control plane shutdown."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

from agent_control_plane.engine.event_store import EventStore
from agent_control_plane.engine.session_manager import SessionManager
from agent_control_plane.types.enums import (
    AbortReason,
    EventKind,
    KillSwitchScope,
    SessionStatus,
)

if TYPE_CHECKING:
    from agent_control_plane.storage.protocols import AsyncApprovalRepository, AsyncSessionRepository

logger = logging.getLogger(__name__)


class KillSwitch:
    """Emergency stop mechanism with 4 scope levels."""

    def __init__(
        self,
        session_manager: SessionManager,
        event_store: EventStore,
        session_repo: AsyncSessionRepository,
        approval_repo: AsyncApprovalRepository,
    ) -> None:
        self.session_manager = session_manager
        self.event_store = event_store
        self._session_repo = session_repo
        self._approval_repo = approval_repo

    async def trigger(
        self,
        scope: KillSwitchScope,
        *,
        session_id: UUID | None = None,
        agent_id: str | None = None,
        reason: str = "Kill switch triggered",
    ) -> dict[str, Any]:
        """Trigger the kill switch at the specified scope."""
        if scope == KillSwitchScope.SESSION_ABORT:
            return await self._abort_session(session_id, reason)
        elif scope == KillSwitchScope.AGENT_ABORT:
            return await self._abort_agent(agent_id, reason)
        elif scope == KillSwitchScope.SYSTEM_HALT:
            return await self._system_halt(reason)
        elif scope == KillSwitchScope.BUDGET_AUTO_HALT:
            return await self._budget_halt(session_id, reason)
        else:
            raise ValueError(f"Unknown kill switch scope: {scope}")

    async def _abort_session(self, session_id: UUID | None, reason: str) -> dict[str, Any]:
        """Stop one session and deny all pending tickets."""
        if session_id is None:
            raise ValueError("session_id required for session_abort")

        await self.session_manager.abort_session(session_id, AbortReason.KILL_SWITCH, reason)
        denied = await self._approval_repo.deny_all_pending(session_id)

        await self.event_store.append(
            session_id=session_id,
            event_kind=EventKind.SESSION_ABORTED,
            payload={"reason": reason, "tickets_denied": denied},
            state_bearing=True,
        )
        return {"scope": KillSwitchScope.SESSION_ABORT, "session_id": str(session_id), "tickets_denied": denied}

    async def _abort_agent(self, agent_id: str | None, reason: str) -> dict[str, Any]:
        """Pause active execution across all sessions for an agent-abort event."""
        if agent_id is None:
            raise ValueError("agent_id required for agent_abort")

        sessions = await self._session_repo.list_sessions(statuses=[SessionStatus.ACTIVE, SessionStatus.CREATED])
        denied = 0
        affected = 0
        for cs in sessions:
            await self.session_manager.set_active_cycle(cs.id, None)
            if cs.status == SessionStatus.ACTIVE:
                await self.session_manager.pause_session(cs.id)
            denied += await self._approval_repo.deny_all_pending(cs.id)
            await self.event_store.append(
                session_id=cs.id,
                event_kind=EventKind.KILL_SWITCH_TRIGGERED,
                payload={"scope": KillSwitchScope.AGENT_ABORT, "agent_id": agent_id, "reason": reason},
                state_bearing=False,
            )
            affected += 1

        return {
            "scope": KillSwitchScope.AGENT_ABORT,
            "agent_id": agent_id,
            "sessions_affected": affected,
            "tickets_denied": denied,
        }

    async def _system_halt(self, reason: str) -> dict[str, Any]:
        """Emergency stop ALL execution system-wide."""
        sessions = await self._session_repo.list_sessions(statuses=[SessionStatus.ACTIVE, SessionStatus.CREATED])

        aborted = 0
        total_denied = 0
        for cs in sessions:
            await self.session_manager.abort_session(cs.id, AbortReason.KILL_SWITCH, reason)
            denied = await self._approval_repo.deny_all_pending(cs.id)
            total_denied += denied
            await self.event_store.append(
                session_id=cs.id,
                event_kind=EventKind.KILL_SWITCH_TRIGGERED,
                payload={"scope": KillSwitchScope.SYSTEM_HALT, "reason": reason},
                state_bearing=True,
            )
            aborted += 1

        logger.critical("SYSTEM HALT: Aborted %d sessions, denied %d tickets", aborted, total_denied)
        return {"scope": KillSwitchScope.SYSTEM_HALT, "sessions_aborted": aborted, "tickets_denied": total_denied}

    async def _budget_halt(self, session_id: UUID | None, reason: str) -> dict[str, Any]:
        """Auto-triggered when session budget is exhausted."""
        if session_id is None:
            raise ValueError("session_id required for budget_auto_halt")

        await self.session_manager.abort_session(session_id, AbortReason.BUDGET_EXHAUSTED, reason)
        denied = await self._approval_repo.deny_all_pending(session_id)

        await self.event_store.append(
            session_id=session_id,
            event_kind=EventKind.BUDGET_EXHAUSTED,
            payload={"reason": reason, "tickets_denied": denied},
            state_bearing=True,
        )
        return {
            "scope": KillSwitchScope.BUDGET_AUTO_HALT,
            "session_id": str(session_id),
            "tickets_denied": denied,
        }
