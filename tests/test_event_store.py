"""Tests for EventStore buffering and fail-closed behavior."""

from uuid import uuid4

import pytest

from agent_control_plane.engine.event_store import EventStore
from agent_control_plane.types.enums import EventKind

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
