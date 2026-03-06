"""Tests for TimeoutEscalation cycle release behavior."""

from uuid import uuid4

import pytest

from agent_control_plane.engine.event_store import EventStore
from agent_control_plane.engine.session_manager import SessionManager
from agent_control_plane.types.enums import SessionStatus

from .fakes import InMemoryEventRepository, InMemorySessionRepository


async def _make_escalation(*, fail_events=False):
    session_repo = InMemorySessionRepository()
    event_repo = InMemoryEventRepository(fail=fail_events)
    sm = SessionManager(session_repo)
    es = EventStore(event_repo)
    from agent_control_plane.recovery.timeout_escalation import TimeoutEscalation

    escalation = TimeoutEscalation(sm, es, session_repo, event_repo)
    return escalation, session_repo, event_repo


@pytest.mark.asyncio
async def test_escalation_releases_cycle_id_before_append():
    escalation, session_repo, event_repo = await _make_escalation()

    cycle_id = uuid4()
    cs = await session_repo.create_session(
        session_name=f"test-{uuid4()}",
        status=SessionStatus.ACTIVE,
        execution_mode="dry_run",
        max_cost=1000,
        max_action_count=50,
        active_cycle_id=cycle_id,
    )
    # Need to set the active_cycle_id since create_session may not pass it through
    await session_repo.set_active_cycle(cs.id, cycle_id)

    await escalation._escalate(cs, "timeout details")

    updated = await session_repo.get_session(cs.id)
    assert updated.active_cycle_id is None
    events = await event_repo.replay(cs.id)
    assert len(events) == 1
    assert events[0].payload["cycle_id"] == str(cycle_id)


@pytest.mark.asyncio
async def test_escalation_still_releases_cycle_when_event_append_fails():
    escalation, session_repo, event_repo = await _make_escalation(fail_events=True)

    cycle_id = uuid4()
    cs = await session_repo.create_session(
        session_name=f"test-{uuid4()}",
        status=SessionStatus.ACTIVE,
        execution_mode="dry_run",
        max_cost=1000,
        max_action_count=50,
    )
    await session_repo.set_active_cycle(cs.id, cycle_id)
    # Update the local cs object too
    object.__setattr__(cs, "active_cycle_id", cycle_id)

    await escalation._escalate(cs, "timeout details")

    updated = await session_repo.get_session(cs.id)
    assert updated.active_cycle_id is None
