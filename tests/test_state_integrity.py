"""Tests for session-state integrity validation on resume and crash recovery.

Persisted session state is reloaded and trusted every time a session resumes or is
crash-recovered. These tests pin the deterministic invariants that must hold before that
state is trusted, and that violations fail closed (raise) and emit a state-bearing audit
event rather than silently resuming a corrupt session.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from agent_control_plane.engine.event_store import EventStore
from agent_control_plane.engine.session_manager import SessionManager
from agent_control_plane.engine.state_integrity import (
    IntegrityViolation,
    SessionStateIntegrityError,
    validate_session_integrity,
)
from agent_control_plane.recovery.crash_recovery import CrashRecovery
from agent_control_plane.types.enums import AbortReason, EventKind, ExecutionMode, SessionStatus
from agent_control_plane.types.sessions import SessionState

from .fakes import InMemoryEventRepository, InMemorySessionRepository


def _state(**overrides) -> SessionState:
    defaults = {
        "id": uuid4(),
        "session_name": "s",
        "status": SessionStatus.PAUSED,
        "execution_mode": ExecutionMode.LIVE,
        "max_cost": Decimal("100"),
        "used_cost": Decimal("10"),
        "max_action_count": 50,
        "used_action_count": 5,
    }
    defaults.update(overrides)
    return SessionState(**defaults)


# --- pure validator -------------------------------------------------------------------


def test_clean_state_has_no_violations():
    assert validate_session_integrity(_state()) == []


def test_violations_are_frozen_dataclasses():
    v = validate_session_integrity(_state(used_cost=Decimal("-1")))
    assert isinstance(v[0], IntegrityViolation)
    with pytest.raises(AttributeError):
        v[0].code = "mutated"  # frozen


@pytest.mark.parametrize(
    ("overrides", "expected_code"),
    [
        ({"used_cost": Decimal("-1")}, "negative_used_cost"),
        ({"used_action_count": -1}, "negative_used_action_count"),
        ({"max_cost": Decimal("-1")}, "negative_max_cost"),
        ({"max_action_count": -1}, "negative_max_action_count"),
        ({"status": SessionStatus.ABORTED, "abort_reason": None}, "aborted_without_reason"),
    ],
)
def test_each_invariant_is_flagged(overrides, expected_code):
    codes = [v.code for v in validate_session_integrity(_state(**overrides))]
    assert expected_code in codes


def test_aborted_with_reason_is_clean():
    state = _state(status=SessionStatus.ABORTED, abort_reason=AbortReason.SYSTEM_ERROR)
    assert validate_session_integrity(state) == []


def test_multiple_violations_all_reported():
    codes = [v.code for v in validate_session_integrity(_state(used_cost=Decimal("-1"), max_action_count=-2))]
    assert "negative_used_cost" in codes
    assert "negative_max_action_count" in codes


# --- resume_session wiring ------------------------------------------------------------


async def _seed_paused(repo: InMemorySessionRepository, **overrides) -> SessionState:
    cs = await repo.create_session(
        session_name="resume-test",
        status=SessionStatus.PAUSED,
        execution_mode=ExecutionMode.LIVE,
        max_cost=Decimal("100"),
        max_action_count=50,
    )
    if overrides:
        await repo.update_session(cs.id, **overrides)
    return await repo.get_session(cs.id)


@pytest.mark.asyncio
async def test_resume_fails_closed_on_corrupt_state():
    repo = InMemorySessionRepository()
    event_repo = InMemoryEventRepository()
    sm = SessionManager(repo, event_store=EventStore(event_repo))
    cs = await _seed_paused(repo, used_cost=Decimal("-5"))

    with pytest.raises(SessionStateIntegrityError):
        await sm.resume_session(cs.id)

    # Not transitioned to ACTIVE — fail closed.
    assert (await repo.get_session(cs.id)).status == SessionStatus.PAUSED
    # State-bearing anomaly event recorded.
    events = await event_repo.replay(cs.id)
    invalid = [e for e in events if e.kind == EventKind.SESSION_STATE_INVALID]
    assert invalid
    assert "negative_used_cost" in str(invalid[-1].payload)


@pytest.mark.asyncio
async def test_resume_clean_session_succeeds():
    repo = InMemorySessionRepository()
    event_repo = InMemoryEventRepository()
    sm = SessionManager(repo, event_store=EventStore(event_repo))
    cs = await _seed_paused(repo)

    resumed = await sm.resume_session(cs.id)

    assert resumed.status == SessionStatus.ACTIVE
    events = await event_repo.replay(cs.id)
    assert not [e for e in events if e.kind == EventKind.SESSION_STATE_INVALID]


@pytest.mark.asyncio
async def test_resume_without_event_store_still_fails_closed():
    # Backward-compatible construction (no event store): still raises, just no audit event.
    repo = InMemorySessionRepository()
    sm = SessionManager(repo)
    cs = await _seed_paused(repo, used_cost=Decimal("-5"))

    with pytest.raises(SessionStateIntegrityError):
        await sm.resume_session(cs.id)
    assert (await repo.get_session(cs.id)).status == SessionStatus.PAUSED


# --- activate_session wiring ----------------------------------------------------------


@pytest.mark.asyncio
async def test_activate_fails_closed_on_corrupt_state():
    repo = InMemorySessionRepository()
    event_repo = InMemoryEventRepository()
    sm = SessionManager(repo, event_store=EventStore(event_repo))
    cs = await repo.create_session(
        session_name="activate-corrupt",
        status=SessionStatus.CREATED,
        execution_mode=ExecutionMode.LIVE,
        max_cost=Decimal("100"),
        max_action_count=50,
    )
    await repo.update_session(cs.id, used_cost=Decimal("-5"))

    with pytest.raises(SessionStateIntegrityError):
        await sm.activate_session(cs.id)

    assert (await repo.get_session(cs.id)).status == SessionStatus.CREATED
    events = await event_repo.replay(cs.id)
    assert any(e.kind == EventKind.SESSION_STATE_INVALID for e in events)


@pytest.mark.asyncio
async def test_activate_clean_session_succeeds():
    repo = InMemorySessionRepository()
    sm = SessionManager(repo, event_store=EventStore(InMemoryEventRepository()))
    cs = await repo.create_session(
        session_name="activate-clean",
        status=SessionStatus.CREATED,
        execution_mode=ExecutionMode.LIVE,
        max_cost=Decimal("100"),
        max_action_count=50,
    )

    activated = await sm.activate_session(cs.id)

    assert activated.status == SessionStatus.ACTIVE


# --- crash recovery wiring ------------------------------------------------------------


@pytest.mark.asyncio
async def test_crash_recovery_aborts_corrupt_session():
    repo = InMemorySessionRepository()
    event_repo = InMemoryEventRepository()
    es = EventStore(event_repo)
    sm = SessionManager(repo, event_store=es)
    cr = CrashRecovery(sm, es, repo, event_repo)

    cs = await repo.create_session(
        session_name="recover-test",
        status=SessionStatus.ACTIVE,
        execution_mode=ExecutionMode.LIVE,
        max_cost=Decimal("100"),
        max_action_count=50,
        active_cycle_id=uuid4(),
    )
    await repo.update_session(cs.id, used_cost=Decimal("-5"))

    result = await cr.recover_on_startup()

    assert result["aborted"] == 1
    assert (await repo.get_session(cs.id)).status == SessionStatus.ABORTED
    events = await event_repo.replay(cs.id)
    assert any(e.kind == EventKind.SESSION_STATE_INVALID for e in events)


@pytest.mark.asyncio
async def test_crash_recovery_recovers_clean_session():
    repo = InMemorySessionRepository()
    event_repo = InMemoryEventRepository()
    es = EventStore(event_repo)
    sm = SessionManager(repo, event_store=es)
    cr = CrashRecovery(sm, es, repo, event_repo)

    cs = await repo.create_session(
        session_name="recover-clean",
        status=SessionStatus.ACTIVE,
        execution_mode=ExecutionMode.LIVE,
        max_cost=Decimal("100"),
        max_action_count=50,
        active_cycle_id=uuid4(),
    )

    result = await cr.recover_on_startup()

    assert result["recovered"] == 1
    assert (await repo.get_session(cs.id)).active_cycle_id is None


@pytest.mark.asyncio
async def test_crash_recovery_validates_non_stuck_active_sessions():
    # An ACTIVE session with no active cycle ("not stuck") but corrupt persisted state must
    # still be caught on startup — otherwise it silently resumes with unvalidated counters.
    repo = InMemorySessionRepository()
    event_repo = InMemoryEventRepository()
    es = EventStore(event_repo)
    sm = SessionManager(repo, event_store=es)
    cr = CrashRecovery(sm, es, repo, event_repo)

    cs = await repo.create_session(
        session_name="non-stuck-corrupt",
        status=SessionStatus.ACTIVE,
        execution_mode=ExecutionMode.LIVE,
        max_cost=Decimal("100"),
        max_action_count=50,
    )
    await repo.update_session(cs.id, used_cost=Decimal("-5"))  # corrupt, no active_cycle_id

    await cr.recover_on_startup()

    assert (await repo.get_session(cs.id)).status == SessionStatus.ABORTED
    events = await event_repo.replay(cs.id)
    assert any(e.kind == EventKind.SESSION_STATE_INVALID for e in events)


@pytest.mark.asyncio
async def test_crash_recovery_leaves_clean_non_stuck_active_sessions_untouched():
    # A clean ACTIVE-non-stuck session must not be disturbed by the startup integrity sweep.
    repo = InMemorySessionRepository()
    event_repo = InMemoryEventRepository()
    es = EventStore(event_repo)
    sm = SessionManager(repo, event_store=es)
    cr = CrashRecovery(sm, es, repo, event_repo)

    cs = await repo.create_session(
        session_name="non-stuck-clean",
        status=SessionStatus.ACTIVE,
        execution_mode=ExecutionMode.LIVE,
        max_cost=Decimal("100"),
        max_action_count=50,
    )

    await cr.recover_on_startup()

    assert (await repo.get_session(cs.id)).status == SessionStatus.ACTIVE
