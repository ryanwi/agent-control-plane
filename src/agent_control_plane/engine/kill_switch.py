"""Kill switch mechanics for emergency control plane shutdown."""

import logging
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agent_control_plane.engine.event_store import EventStore
from agent_control_plane.engine.session_manager import SessionManager
from agent_control_plane.models.registry import ModelRegistry
from agent_control_plane.types.enums import (
    AbortReason,
    ApprovalStatus,
    EventKind,
    KillSwitchScope,
    SessionStatus,
)

logger = logging.getLogger(__name__)


class KillSwitch:
    """Emergency stop mechanism with 4 scope levels."""

    def __init__(
        self,
        session_manager: SessionManager,
        event_store: EventStore,
    ) -> None:
        self.session_manager = session_manager
        self.event_store = event_store

    async def trigger(
        self,
        db_session: AsyncSession,
        scope: KillSwitchScope,
        *,
        session_id: UUID | None = None,
        agent_id: str | None = None,
        reason: str = "Kill switch triggered",
    ) -> dict:
        """Trigger the kill switch at the specified scope."""
        if scope == KillSwitchScope.SESSION_ABORT:
            return await self._abort_session(db_session, session_id, reason)
        elif scope == KillSwitchScope.AGENT_ABORT:
            return await self._abort_agent(db_session, agent_id, reason)
        elif scope == KillSwitchScope.SYSTEM_HALT:
            return await self._system_halt(db_session, reason)
        elif scope == KillSwitchScope.BUDGET_AUTO_HALT:
            return await self._budget_halt(db_session, session_id, reason)
        else:
            raise ValueError(f"Unknown kill switch scope: {scope}")

    async def _abort_session(self, db_session: AsyncSession, session_id: UUID | None, reason: str) -> dict:
        """Stop one session and deny all pending tickets."""
        if session_id is None:
            raise ValueError("session_id required for session_abort")

        await self.session_manager.abort_session(db_session, session_id, AbortReason.KILL_SWITCH, reason)
        denied = await self._deny_pending_tickets(db_session, session_id)

        await self.event_store.append(
            db_session,
            session_id=session_id,
            event_kind=EventKind.SESSION_ABORTED,
            payload={"reason": reason, "tickets_denied": denied},
            state_bearing=True,
        )
        return {"scope": "session_abort", "session_id": str(session_id), "tickets_denied": denied}

    async def _abort_agent(self, db_session: AsyncSession, agent_id: str | None, reason: str) -> dict:
        """Pause active execution across all sessions for an agent-abort event.

        This scope intentionally affects every active/created session as a
        safety-first halt. `agent_id` is recorded for audit/logical grouping.
        """
        if agent_id is None:
            raise ValueError("agent_id required for agent_abort")

        ControlSession = ModelRegistry.get("ControlSession")
        result = await db_session.execute(
            select(ControlSession).where(ControlSession.status.in_([SessionStatus.ACTIVE, SessionStatus.CREATED]))
        )
        sessions = list(result.scalars().all())
        denied = 0
        affected = 0
        for cs in sessions:
            await self.session_manager.set_active_cycle(db_session, cs.id, None)
            if cs.status == SessionStatus.ACTIVE:
                await self.session_manager.pause_session(db_session, cs.id)
            denied += await self._deny_pending_tickets(db_session, cs.id)
            await self.event_store.append(
                db_session,
                session_id=cs.id,
                event_kind=EventKind.KILL_SWITCH_TRIGGERED,
                payload={"scope": "agent_abort", "agent_id": agent_id, "reason": reason},
                state_bearing=False,
            )
            affected += 1

        return {
            "scope": "agent_abort",
            "agent_id": agent_id,
            "sessions_affected": affected,
            "tickets_denied": denied,
        }

    async def _system_halt(self, db_session: AsyncSession, reason: str) -> dict:
        """Emergency stop ALL execution system-wide."""
        ControlSession = ModelRegistry.get("ControlSession")
        result = await db_session.execute(
            select(ControlSession).where(ControlSession.status.in_([SessionStatus.ACTIVE, SessionStatus.CREATED]))
        )
        sessions = list(result.scalars().all())

        aborted = 0
        total_denied = 0
        for cs in sessions:
            await self.session_manager.abort_session(db_session, cs.id, AbortReason.KILL_SWITCH, reason)
            denied = await self._deny_pending_tickets(db_session, cs.id)
            total_denied += denied
            await self.event_store.append(
                db_session,
                session_id=cs.id,
                event_kind=EventKind.KILL_SWITCH_TRIGGERED,
                payload={"scope": "system_halt", "reason": reason},
                state_bearing=True,
            )
            aborted += 1

        logger.critical("SYSTEM HALT: Aborted %d sessions, denied %d tickets", aborted, total_denied)
        return {"scope": "system_halt", "sessions_aborted": aborted, "tickets_denied": total_denied}

    async def _budget_halt(self, db_session: AsyncSession, session_id: UUID | None, reason: str) -> dict:
        """Auto-triggered when session budget is exhausted."""
        if session_id is None:
            raise ValueError("session_id required for budget_auto_halt")

        await self.session_manager.abort_session(db_session, session_id, AbortReason.BUDGET_EXHAUSTED, reason)
        denied = await self._deny_pending_tickets(db_session, session_id)

        await self.event_store.append(
            db_session,
            session_id=session_id,
            event_kind=EventKind.BUDGET_EXHAUSTED,
            payload={"reason": reason, "tickets_denied": denied},
            state_bearing=True,
        )
        return {
            "scope": "budget_auto_halt",
            "session_id": str(session_id),
            "tickets_denied": denied,
        }

    async def _deny_pending_tickets(self, db_session: AsyncSession, session_id: UUID) -> int:
        """Deny all pending approval tickets for a session."""
        ApprovalTicket = ModelRegistry.get("ApprovalTicket")
        result = await db_session.execute(
            update(ApprovalTicket)
            .where(
                ApprovalTicket.session_id == session_id,
                ApprovalTicket.status == ApprovalStatus.PENDING,
            )
            .values(status=ApprovalStatus.DENIED, decision_reason="Kill switch triggered")
            .returning(ApprovalTicket.id)
        )
        denied_ids = list(result.scalars().all())
        return len(denied_ids)
