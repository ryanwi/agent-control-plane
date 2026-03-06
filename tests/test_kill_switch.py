"""Tests for KillSwitch dispatch, validation, and scope behavior."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from agent_control_plane.engine.event_store import EventStore
from agent_control_plane.engine.kill_switch import KillSwitch
from agent_control_plane.engine.session_manager import SessionManager
from agent_control_plane.types.enums import (
    AbortReason,
    EventKind,
    KillSwitchScope,
    SessionStatus,
)


class _FakeControlSession:
    def __init__(self, *, status=SessionStatus.ACTIVE):
        self.id = uuid4()
        self.status = status


class _TrackingSessionManager(SessionManager):
    """SessionManager that tracks calls without DB."""

    def __init__(self):
        self.aborted = []
        self.paused = []
        self.cycle_clears = []

    async def abort_session(self, session, session_id, reason, details=None):
        self.aborted.append({"session_id": session_id, "reason": reason, "details": details})

    async def pause_session(self, session, session_id):
        self.paused.append(session_id)

    async def set_active_cycle(self, session, session_id, cycle_id):
        self.cycle_clears.append({"session_id": session_id, "cycle_id": cycle_id})


class _TrackingEventStore(EventStore):
    """EventStore that tracks appends without DB."""

    def __init__(self):
        super().__init__()
        self.appended = []

    async def append(self, session, session_id, event_kind, payload, **kwargs):
        self.appended.append({"session_id": session_id, "event_kind": event_kind, "payload": payload})


def _make_ks():
    sm = _TrackingSessionManager()
    es = _TrackingEventStore()
    ks = KillSwitch(sm, es)
    ks._deny_pending_tickets = AsyncMock(return_value=0)
    return ks, sm, es


def _patch_session_query(ks, sessions):
    """Patch _system_halt and _abort_agent to use provided sessions instead of DB query."""

    async def patched_system_halt(db_session, reason):
        for cs in sessions:
            await ks.session_manager.abort_session(db_session, cs.id, AbortReason.KILL_SWITCH, reason)
            await ks._deny_pending_tickets(db_session, cs.id)
            await ks.event_store.append(
                db_session,
                session_id=cs.id,
                event_kind=EventKind.KILL_SWITCH_TRIGGERED,
                payload={"scope": "system_halt", "reason": reason},
                state_bearing=True,
            )
        return {"scope": "system_halt", "sessions_aborted": len(sessions), "tickets_denied": 0}

    async def patched_abort_agent(db_session, agent_id, reason):
        if agent_id is None:
            raise ValueError("agent_id required for agent_abort")
        affected = 0
        for cs in sessions:
            await ks.session_manager.set_active_cycle(db_session, cs.id, None)
            if cs.status == SessionStatus.ACTIVE:
                await ks.session_manager.pause_session(db_session, cs.id)
            await ks._deny_pending_tickets(db_session, cs.id)
            await ks.event_store.append(
                db_session,
                session_id=cs.id,
                event_kind=EventKind.KILL_SWITCH_TRIGGERED,
                payload={"scope": "agent_abort", "agent_id": agent_id, "reason": reason},
                state_bearing=False,
            )
            affected += 1
        return {"scope": "agent_abort", "agent_id": agent_id, "sessions_affected": affected, "tickets_denied": 0}

    ks._system_halt = patched_system_halt
    ks._abort_agent = patched_abort_agent


class TestKillSwitchValidation:
    @pytest.mark.asyncio
    async def test_session_abort_requires_session_id(self):
        ks, _, _ = _make_ks()
        with pytest.raises(ValueError, match="session_id required"):
            await ks.trigger(AsyncMock(), KillSwitchScope.SESSION_ABORT)

    @pytest.mark.asyncio
    async def test_agent_abort_requires_agent_id(self):
        ks, _, _ = _make_ks()
        with pytest.raises(ValueError, match="agent_id required"):
            await ks.trigger(AsyncMock(), KillSwitchScope.AGENT_ABORT)

    @pytest.mark.asyncio
    async def test_budget_halt_requires_session_id(self):
        ks, _, _ = _make_ks()
        with pytest.raises(ValueError, match="session_id required"):
            await ks.trigger(AsyncMock(), KillSwitchScope.BUDGET_AUTO_HALT)


class TestSessionAbort:
    @pytest.mark.asyncio
    async def test_aborts_session_and_emits_event(self):
        ks, sm, es = _make_ks()
        sid = uuid4()
        result = await ks.trigger(AsyncMock(), KillSwitchScope.SESSION_ABORT, session_id=sid, reason="test halt")

        assert result["scope"] == "session_abort"
        assert result["session_id"] == str(sid)
        assert len(sm.aborted) == 1
        assert sm.aborted[0]["reason"] == AbortReason.KILL_SWITCH
        assert sm.aborted[0]["details"] == "test halt"
        assert len(es.appended) == 1
        assert es.appended[0]["event_kind"] == EventKind.SESSION_ABORTED


class TestBudgetHalt:
    @pytest.mark.asyncio
    async def test_uses_budget_exhausted_reason(self):
        ks, sm, es = _make_ks()
        sid = uuid4()
        result = await ks.trigger(AsyncMock(), KillSwitchScope.BUDGET_AUTO_HALT, session_id=sid)

        assert result["scope"] == "budget_auto_halt"
        assert sm.aborted[0]["reason"] == AbortReason.BUDGET_EXHAUSTED
        assert es.appended[0]["event_kind"] == EventKind.BUDGET_EXHAUSTED


class TestSystemHalt:
    @pytest.mark.asyncio
    async def test_aborts_all_active_sessions(self):
        ks, sm, es = _make_ks()
        cs1 = _FakeControlSession(status=SessionStatus.ACTIVE)
        cs2 = _FakeControlSession(status=SessionStatus.CREATED)
        _patch_session_query(ks, [cs1, cs2])

        result = await ks.trigger(AsyncMock(), KillSwitchScope.SYSTEM_HALT, reason="emergency")

        assert result["scope"] == "system_halt"
        assert result["sessions_aborted"] == 2
        assert len(sm.aborted) == 2
        assert len(es.appended) == 2


class TestAgentAbort:
    @pytest.mark.asyncio
    async def test_pauses_active_and_clears_cycles(self):
        ks, sm, es = _make_ks()
        cs = _FakeControlSession(status=SessionStatus.ACTIVE)
        _patch_session_query(ks, [cs])

        result = await ks.trigger(AsyncMock(), KillSwitchScope.AGENT_ABORT, agent_id="agent-1", reason="stop")

        assert result["scope"] == "agent_abort"
        assert result["sessions_affected"] == 1
        assert len(sm.paused) == 1
        assert len(sm.cycle_clears) == 1
        assert sm.cycle_clears[0]["cycle_id"] is None

    @pytest.mark.asyncio
    async def test_does_not_pause_created_sessions(self):
        ks, sm, es = _make_ks()
        cs = _FakeControlSession(status=SessionStatus.CREATED)
        _patch_session_query(ks, [cs])

        result = await ks.trigger(AsyncMock(), KillSwitchScope.AGENT_ABORT, agent_id="agent-1", reason="stop")

        assert result["sessions_affected"] == 1
        assert len(sm.paused) == 0
        assert len(sm.cycle_clears) == 1
