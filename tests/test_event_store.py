"""Tests for EventStore buffering and fail-closed behavior."""

from uuid import uuid4

import pytest

from agent_control_plane.engine.event_store import EventStore
from agent_control_plane.engine.state_integrity import SessionStateIntegrityError
from agent_control_plane.types.enums import EventKind
from agent_control_plane.types.frames import EventFrame

from .fakes import InMemoryEventRepository


@pytest.mark.asyncio
async def test_append_buffers_telemetry_events_when_non_state_bearing_failures_occur():
    repo = InMemoryEventRepository(fail=True)
    store = EventStore(repo)

    session_id = uuid4()
    result = await store.append(
        session_id=session_id,
        event_kind=EventKind.CYCLE_STARTED,
        payload={"source": "test"},
        state_bearing=False,
    )

    assert result is None
    assert store.buffer_size == 1
    assert store._buffer[0]["event_kind"] == EventKind.CYCLE_STARTED
    assert store._buffer[0]["session_id"] == session_id


@pytest.mark.asyncio
async def test_append_raises_for_state_bearing_failures():
    repo = InMemoryEventRepository(fail=True)
    store = EventStore(repo)

    with pytest.raises(RuntimeError):
        await store.append(
            session_id=uuid4(),
            event_kind=EventKind.CYCLE_STARTED,
            payload={},
            state_bearing=True,
        )


@pytest.mark.asyncio
async def test_append_returns_seq_on_success():
    repo = InMemoryEventRepository()
    store = EventStore(repo)
    session_id = uuid4()

    seq = await store.append(
        session_id=session_id,
        event_kind=EventKind.CYCLE_STARTED,
        payload={"test": True},
    )
    assert seq == 1

    seq2 = await store.append(
        session_id=session_id,
        event_kind=EventKind.CYCLE_COMPLETED,
        payload={},
    )
    assert seq2 == 2


@pytest.mark.asyncio
async def test_replay_raises_on_sequence_gap():
    """replay() must raise SessionStateIntegrityError when seq numbers have a gap."""
    repo = InMemoryEventRepository()
    store = EventStore(repo)
    session_id = uuid4()

    # Append two legitimate events (seq 1, 2), then inject a gap by skipping to seq 4.
    await store.append(session_id, EventKind.CYCLE_STARTED, {})
    await store.append(session_id, EventKind.RISK_ASSESSED, {})
    # Manually inject an event with a non-consecutive seq to simulate tampering.
    repo._events[session_id].append(
        EventFrame(session_id=session_id, seq=4, kind=EventKind.CYCLE_COMPLETED, payload={})
    )

    with pytest.raises(SessionStateIntegrityError):
        await store.replay(session_id)


@pytest.mark.asyncio
async def test_replay_raises_on_duplicate_sequence():
    """replay() must raise SessionStateIntegrityError when seq numbers duplicate."""
    repo = InMemoryEventRepository()
    store = EventStore(repo)
    session_id = uuid4()

    await store.append(session_id, EventKind.CYCLE_STARTED, {})
    # Inject a duplicate seq=1.
    repo._events[session_id].append(EventFrame(session_id=session_id, seq=1, kind=EventKind.RISK_ASSESSED, payload={}))

    with pytest.raises(SessionStateIntegrityError):
        await store.replay(session_id)


@pytest.mark.asyncio
async def test_replay_normal_sequence_passes():
    """replay() must not raise for a well-formed consecutive sequence."""
    repo = InMemoryEventRepository()
    store = EventStore(repo)
    session_id = uuid4()

    await store.append(session_id, EventKind.CYCLE_STARTED, {})
    await store.append(session_id, EventKind.RISK_ASSESSED, {})
    await store.append(session_id, EventKind.CYCLE_COMPLETED, {})

    events = await store.replay(session_id)
    assert [e.seq for e in events] == [1, 2, 3]
