"""Tests for pre-execution precondition verification."""

from __future__ import annotations

from uuid import uuid4

import pytest

from agent_control_plane.engine.event_store import EventStore
from agent_control_plane.engine.precondition_verifier import PreconditionVerifier
from agent_control_plane.types.enums import EventKind
from agent_control_plane.types.preconditions import Precondition, PreconditionStatus

from .fakes import InMemoryEventRepository


class _MapProvider:
    provider_id = "map"

    def __init__(self, states: dict[str, object]) -> None:
        self._states = states

    def read_state(self, precondition: Precondition) -> object:
        return self._states[precondition.resource_id]


@pytest.mark.asyncio
async def test_verify_skips_when_no_preconditions():
    repo = InMemoryEventRepository()
    verifier = PreconditionVerifier(EventStore(repo), providers=[_MapProvider({})])
    session_id = uuid4()

    result = await verifier.verify(session_id, [])

    assert result.status == PreconditionStatus.SKIPPED
    assert result.checked_count == 0
    assert result.divergences == []
    assert await repo.replay(session_id) == []


@pytest.mark.asyncio
async def test_verify_passes_when_all_preconditions_match():
    repo = InMemoryEventRepository()
    verifier = PreconditionVerifier(EventStore(repo), providers=[_MapProvider({"file": "abc"})])
    session_id = uuid4()

    result = await verifier.verify(
        session_id,
        [Precondition(resource_id="file", provider_id="map", expected_state="abc")],
    )

    assert result.status == PreconditionStatus.PASSED
    assert result.checked_count == 1
    assert result.divergences == []
    assert await repo.replay(session_id) == []


@pytest.mark.asyncio
async def test_verify_records_single_precondition_failure():
    repo = InMemoryEventRepository()
    store = EventStore(repo)
    verifier = PreconditionVerifier(store, providers=[_MapProvider({"file": "actual"})])
    session_id = uuid4()

    result = await verifier.verify(
        session_id,
        [Precondition(resource_id="file", provider_id="map", expected_state="expected")],
        proposal_id=uuid4(),
        action_id="train",
    )

    assert result.status == PreconditionStatus.FAILED
    assert len(result.divergences) == 1
    event = (await store.replay(session_id))[0]
    assert event.kind == EventKind.PRECONDITION_FAILED
    assert event.state_bearing is True
    assert event.payload["status"] == "failed"
    assert event.payload["divergences"][0]["expected_state"] == "expected"
    assert event.payload["divergences"][0]["actual_state"] == "actual"


@pytest.mark.asyncio
async def test_verify_records_only_failed_divergences_for_partial_failure():
    repo = InMemoryEventRepository()
    store = EventStore(repo)
    verifier = PreconditionVerifier(store, providers=[_MapProvider({"ok": "same", "bad": "changed"})])
    session_id = uuid4()

    result = await verifier.verify(
        session_id,
        [
            Precondition(resource_id="ok", provider_id="map", expected_state="same"),
            Precondition(resource_id="bad", provider_id="map", expected_state="old"),
        ],
    )

    assert result.status == PreconditionStatus.FAILED
    assert result.checked_count == 2
    assert [d.resource_id for d in result.divergences] == ["bad"]
    event = (await store.replay(session_id))[0]
    assert [d["resource_id"] for d in event.payload["divergences"]] == ["bad"]


@pytest.mark.asyncio
async def test_verify_failure_event_is_state_bearing_and_fails_closed():
    repo = InMemoryEventRepository(fail=True)
    verifier = PreconditionVerifier(EventStore(repo), providers=[_MapProvider({"file": "actual"})])

    with pytest.raises(RuntimeError):
        await verifier.verify(
            uuid4(),
            [Precondition(resource_id="file", provider_id="map", expected_state="expected")],
        )
