from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_control_plane.recovery.timeout_escalation import TimeoutEscalation


class _FakeEventStore:
    def __init__(self, should_fail: bool = False):
        self.should_fail = should_fail
        self.calls = []

    async def append(self, *_args, **kwargs):
        self.calls.append(
            {
                "session_id": kwargs["session_id"],
                "event_kind": kwargs["event_kind"],
                "payload": kwargs.get("payload", {}),
                "state_bearing": kwargs.get("state_bearing"),
            }
        )
        if self.should_fail:
            raise RuntimeError("append failed")


class _FakeSession:
    pass


@pytest.mark.asyncio
async def test_escalation_releases_cycle_id_before_append():
    class _SessionManager:
        pass

    session_id = uuid4()
    cycle_id = uuid4()
    cs = SimpleNamespace(id=session_id, active_cycle_id=cycle_id)

    event_store = _FakeEventStore()
    escalation = TimeoutEscalation(
        session_manager=_SessionManager(),
        event_store=event_store,
    )

    await escalation._escalate(_FakeSession(), cs, "timeout details")

    assert cs.active_cycle_id is None
    assert len(event_store.calls) == 1
    assert event_store.calls[0]["payload"]["cycle_id"] == str(cycle_id)


@pytest.mark.asyncio
async def test_escalation_still_releases_cycle_when_event_append_fails():
    class _SessionManager:
        pass

    session_id = uuid4()
    cycle_id = uuid4()
    cs = SimpleNamespace(id=session_id, active_cycle_id=cycle_id)

    event_store = _FakeEventStore(should_fail=True)
    escalation = TimeoutEscalation(
        session_manager=_SessionManager(),
        event_store=event_store,
    )

    await escalation._escalate(_FakeSession(), cs, "timeout details")

    assert cs.active_cycle_id is None
    assert len(event_store.calls) == 1
