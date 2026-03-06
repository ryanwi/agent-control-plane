"""Tests for KillSwitch dispatch, validation, and scope behavior."""

from decimal import Decimal
from uuid import uuid4

import pytest

from agent_control_plane.engine.event_store import EventStore
from agent_control_plane.engine.kill_switch import KillSwitch
from agent_control_plane.engine.session_manager import SessionManager
from agent_control_plane.types.enums import (
    EventKind,
    KillSwitchScope,
    SessionStatus,
)

from .fakes import InMemoryApprovalRepository, InMemoryEventRepository, InMemorySessionRepository


async def _make_ks(sessions=None):
    """Create a KillSwitch with in-memory repos and optional pre-loaded sessions."""
    session_repo = InMemorySessionRepository()
    event_repo = InMemoryEventRepository()
    approval_repo = InMemoryApprovalRepository()

    sm = SessionManager(session_repo)
    es = EventStore(event_repo)
    ks = KillSwitch(sm, es, session_repo, approval_repo)

    created_sessions = []
    if sessions:
        for s in sessions:
            cs = await session_repo.create_session(
                session_name=f"test-{uuid4()}",
                status=s["status"],
                execution_mode="dry_run",
                max_cost=Decimal("1000"),
                max_action_count=50,
            )
            created_sessions.append(cs)

    return ks, sm, es, session_repo, event_repo, approval_repo, created_sessions


class TestKillSwitchValidation:
    @pytest.mark.asyncio
    async def test_session_abort_requires_session_id(self):
        ks, *_ = await _make_ks()
        with pytest.raises(ValueError, match="session_id required"):
            await ks.trigger(KillSwitchScope.SESSION_ABORT)

    @pytest.mark.asyncio
    async def test_agent_abort_requires_agent_id(self):
        ks, *_ = await _make_ks()
        with pytest.raises(ValueError, match="agent_id required"):
            await ks.trigger(KillSwitchScope.AGENT_ABORT)

    @pytest.mark.asyncio
    async def test_budget_halt_requires_session_id(self):
        ks, *_ = await _make_ks()
        with pytest.raises(ValueError, match="session_id required"):
            await ks.trigger(KillSwitchScope.BUDGET_AUTO_HALT)


class TestSessionAbort:
    @pytest.mark.asyncio
    async def test_aborts_session_and_emits_event(self):
        ks, sm, es, session_repo, event_repo, *_ = await _make_ks([{"status": SessionStatus.ACTIVE}])
        sid = list(session_repo._sessions.keys())[0]
        result = await ks.trigger(KillSwitchScope.SESSION_ABORT, session_id=sid, reason="test halt")

        assert result["scope"] == KillSwitchScope.SESSION_ABORT
        assert result["session_id"] == str(sid)
        cs = await session_repo.get_session(sid)
        assert cs.status == SessionStatus.ABORTED
        events = await event_repo.replay(sid)
        assert any(e.event_kind == EventKind.SESSION_ABORTED for e in events)


class TestBudgetHalt:
    @pytest.mark.asyncio
    async def test_uses_budget_exhausted_reason(self):
        ks, sm, es, session_repo, event_repo, *_ = await _make_ks([{"status": SessionStatus.ACTIVE}])
        sid = list(session_repo._sessions.keys())[0]
        result = await ks.trigger(KillSwitchScope.BUDGET_AUTO_HALT, session_id=sid)

        assert result["scope"] == KillSwitchScope.BUDGET_AUTO_HALT
        events = await event_repo.replay(sid)
        assert any(e.event_kind == EventKind.BUDGET_EXHAUSTED for e in events)


class TestSystemHalt:
    @pytest.mark.asyncio
    async def test_aborts_all_active_sessions(self):
        ks, sm, es, session_repo, event_repo, *_ = await _make_ks(
            [
                {"status": SessionStatus.ACTIVE},
                {"status": SessionStatus.CREATED},
            ]
        )
        result = await ks.trigger(KillSwitchScope.SYSTEM_HALT, reason="emergency")

        assert result["scope"] == KillSwitchScope.SYSTEM_HALT
        assert result["sessions_aborted"] == 2
        for cs in session_repo._sessions.values():
            assert cs.status == SessionStatus.ABORTED


class TestAgentAbort:
    @pytest.mark.asyncio
    async def test_pauses_active_and_clears_cycles(self):
        ks, sm, es, session_repo, event_repo, *_ = await _make_ks([{"status": SessionStatus.ACTIVE}])
        result = await ks.trigger(KillSwitchScope.AGENT_ABORT, agent_id="agent-1", reason="stop")

        assert result["scope"] == KillSwitchScope.AGENT_ABORT
        assert result["sessions_affected"] == 1
        for cs in session_repo._sessions.values():
            assert cs.status == SessionStatus.PAUSED

    @pytest.mark.asyncio
    async def test_does_not_pause_created_sessions(self):
        ks, sm, es, session_repo, event_repo, *_ = await _make_ks([{"status": SessionStatus.CREATED}])
        result = await ks.trigger(KillSwitchScope.AGENT_ABORT, agent_id="agent-1", reason="stop")

        assert result["sessions_affected"] == 1
        for cs in session_repo._sessions.values():
            assert cs.status == SessionStatus.CREATED
