"""Facade exposure of per-session agent revocation (sync + async)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_control_plane.async_facade import AsyncControlPlaneFacade
from agent_control_plane.sync import ControlPlaneFacade, SyncControlPlane
from agent_control_plane.types.enums import EventKind


@pytest.mark.asyncio
async def test_async_facade_revoke_reinstate(tmp_path: Path):
    facade = AsyncControlPlaneFacade.from_database_url(f"sqlite+aiosqlite:///{tmp_path / 'rev.db'}")
    sid = await facade.sessions.open_session("rev")

    assert await facade.agents.is_revoked(sid, "agent-x") is False

    await facade.agents.revoke(sid, "agent-x", reason="suspected compromise")
    assert await facade.agents.is_revoked(sid, "agent-x") is True

    events = await facade.sessions.replay(sid)
    revoked = [e for e in events if e.kind == EventKind.AGENT_REVOKED]
    assert revoked
    assert revoked[-1].state_bearing is True
    assert revoked[-1].payload["agent_id"] == "agent-x"

    await facade.agents.reinstate(sid, "agent-x")
    assert await facade.agents.is_revoked(sid, "agent-x") is False
    assert any(e.kind == EventKind.AGENT_REINSTATED for e in await facade.sessions.replay(sid))

    await facade.close()


def test_sync_facade_revoke_reinstate(tmp_path: Path):
    cp = SyncControlPlane(f"sqlite:///{tmp_path / 'revs.db'}")
    cp.setup()
    facade = ControlPlaneFacade(cp)
    sid = cp.create_session("rev")

    assert facade.agents.is_revoked(sid, "agent-x") is False
    facade.agents.revoke(sid, "agent-x", reason="suspected compromise")
    assert facade.agents.is_revoked(sid, "agent-x") is True
    facade.agents.reinstate(sid, "agent-x")
    assert facade.agents.is_revoked(sid, "agent-x") is False

    cp.close()
