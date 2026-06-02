"""Tests for per-session agent revocation (AgentSessionGuard)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from agent_control_plane.engine.agent_registry import AgentSessionGuard
from agent_control_plane.engine.event_store import EventStore
from agent_control_plane.types.enums import EventKind

from .fakes import InMemoryAgentRepository, InMemoryEventRepository


def _guard() -> tuple[AgentSessionGuard, InMemoryEventRepository]:
    event_repo = InMemoryEventRepository()
    guard = AgentSessionGuard(InMemoryAgentRepository(), EventStore(event_repo))
    return guard, event_repo


@pytest.mark.asyncio
async def test_revoke_then_is_revoked():
    guard, _ = _guard()
    sid = uuid4()
    assert await guard.is_revoked(sid, "agent-1") is False

    await guard.revoke(sid, "agent-1", reason="suspected compromise")

    assert await guard.is_revoked(sid, "agent-1") is True


@pytest.mark.asyncio
async def test_revocation_is_session_scoped():
    guard, _ = _guard()
    sid_a, sid_b = uuid4(), uuid4()

    await guard.revoke(sid_a, "agent-1", reason="x")

    assert await guard.is_revoked(sid_a, "agent-1") is True
    # Revoking in one session must not affect another.
    assert await guard.is_revoked(sid_b, "agent-1") is False


@pytest.mark.asyncio
async def test_revoke_emits_state_bearing_event():
    guard, event_repo = _guard()
    sid = uuid4()

    await guard.revoke(sid, "agent-1", reason="suspected compromise")

    events = await event_repo.replay(sid)
    revoked = [e for e in events if e.kind == EventKind.AGENT_REVOKED]
    assert revoked
    assert revoked[-1].state_bearing is True
    assert revoked[-1].payload["agent_id"] == "agent-1"
    assert "suspected compromise" in str(revoked[-1].payload)


@pytest.mark.asyncio
async def test_reinstate_clears_revocation_and_audits():
    guard, event_repo = _guard()
    sid = uuid4()

    await guard.revoke(sid, "agent-1", reason="x")
    await guard.reinstate(sid, "agent-1")

    assert await guard.is_revoked(sid, "agent-1") is False
    events = await event_repo.replay(sid)
    assert any(e.kind == EventKind.AGENT_REINSTATED for e in events)


@pytest.mark.asyncio
async def test_revocation_round_trip_through_async_backend(tmp_path):
    """The revocation primitive persists through the real async SQLAlchemy backend."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from agent_control_plane.models.reference import Base, register_models
    from agent_control_plane.storage.sqlalchemy_async import AsyncSqlAlchemyAgentRepo

    register_models()
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rev.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sid = uuid4()
    async with async_sessionmaker(bind=engine, expire_on_commit=False)() as session:
        repo = AsyncSqlAlchemyAgentRepo(session)
        assert await repo.is_agent_revoked(sid, "agent-1") is False
        await repo.record_revocation(sid, "agent-1", "compromise")
        await repo.record_revocation(sid, "agent-1", "compromise")  # idempotent
        await session.commit()
        assert await repo.is_agent_revoked(sid, "agent-1") is True
        await repo.clear_revocation(sid, "agent-1")
        await session.commit()
        assert await repo.is_agent_revoked(sid, "agent-1") is False
    await engine.dispose()
