import pytest
from sqlalchemy.exc import OperationalError
from uuid import uuid4

from agent_control_plane.engine.event_store import EventStore


class _FlakySession:
    def add(self, *_args, **_kwargs):
        raise AssertionError("add() must not be called when _allocate_seq fails")

    async def flush(self):
        raise AssertionError("flush() must not be called when _allocate_seq fails")


@pytest.mark.asyncio
async def test_append_buffers_telemetry_events_when_non_state_bearing_failures_occur(monkeypatch):
    store = EventStore()

    async def _allocate_seq(*_args, **_kwargs):
        raise OperationalError("INSERT", {}, Exception("db unavailable"))

    monkeypatch.setattr(store, "_allocate_seq", _allocate_seq)

    session_id = uuid4()
    result = await store.append(
        _FlakySession(),
        session_id=session_id,
        event_kind="cycle_started",
        payload={"source": "test"},
        state_bearing=False,
    )

    assert result is None
    assert store.buffer_size == 1
    assert store._buffer[0]["event_kind"] == "cycle_started"
    assert store._buffer[0]["session_id"] == session_id


@pytest.mark.asyncio
async def test_append_raises_for_state_bearing_failures(monkeypatch):
    store = EventStore()

    async def _allocate_seq(*_args, **_kwargs):
        raise OperationalError("INSERT", {}, Exception("db unavailable"))

    monkeypatch.setattr(store, "_allocate_seq", _allocate_seq)

    with pytest.raises(OperationalError):
        await store.append(
            _FlakySession(),
            session_id=uuid4(),
            event_kind="cycle_started",
            payload={},
            state_bearing=True,
        )
