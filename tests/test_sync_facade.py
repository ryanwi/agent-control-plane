"""Tests for sync facade APIs and app-event mapping."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from agent_control_plane.sync import (
    AppEventMapper,
    ControlPlaneFacade,
    DictEventMapper,
    MappedEventDTO,
    SyncControlPlane,
    UnknownAppEventError,
)
from agent_control_plane.types.enums import EventKind, UnknownAppEventPolicy


def test_sync_control_plane_emit_and_replay_round_trip(tmp_path: Path):
    db_file = tmp_path / "cp_sync_events.db"
    cp = SyncControlPlane(f"sqlite:///{db_file}")
    cp.setup()

    sid = cp.create_session("sync-events", max_cost=Decimal("100"), max_action_count=10)
    seq = cp.emit_event(sid, EventKind.CYCLE_STARTED, {"phase": "begin"}, state_bearing=True)
    assert seq == 1

    events = cp.replay_events(sid)
    assert len(events) == 1
    assert events[0].event_kind == EventKind.CYCLE_STARTED
    assert events[0].payload["phase"] == "begin"
    assert events[0].state_bearing is True
    cp.close()


def test_sync_control_plane_emit_app_event_mapper_and_unknown_policy(tmp_path: Path):
    db_file = tmp_path / "cp_sync_app_events.db"
    cp = SyncControlPlane(f"sqlite:///{db_file}")
    cp.setup()

    sid = cp.create_session("sync-app-events", max_cost=Decimal("100"), max_action_count=10)
    mapper = DictEventMapper({"plan_started": EventKind.CYCLE_STARTED})

    seq = cp.emit_app_event(
        sid,
        "plan_started",
        {"plan_id": "p1"},
        mapper=mapper,
        unknown_policy=UnknownAppEventPolicy.RAISE,
    )
    assert seq == 1

    ignored = cp.emit_app_event(
        sid,
        "unmapped_event",
        {"x": 1},
        mapper=mapper,
        unknown_policy=UnknownAppEventPolicy.IGNORE,
    )
    assert ignored is None

    with pytest.raises(UnknownAppEventError):
        cp.emit_app_event(
            sid,
            "unmapped_event",
            {"x": 2},
            mapper=mapper,
            unknown_policy=UnknownAppEventPolicy.RAISE,
        )

    tagged_seq = cp.emit_app_event(
        sid,
        "plan_started",
        {"plan_id": "p2"},
        mapper=mapper,
        unknown_policy=UnknownAppEventPolicy.RAISE,
        state_bearing=True,
        agent_id="agent-42",
        correlation_id=uuid4(),
        idempotency_key="idem-1",
    )
    assert tagged_seq == 2

    tagged_event = cp.replay_events(sid, after_seq=1)[0]
    assert tagged_event.agent_id == "agent-42"
    assert tagged_event.state_bearing is True
    cp.close()


class _SecurityMapper(AppEventMapper):
    def map_event(self, event_name: str, payload: Mapping[str, Any]) -> MappedEventDTO | None:
        if event_name == "scan_started":
            return DictEventMapper({"scan_started": EventKind.CYCLE_STARTED}).map_event(event_name, payload)
        if event_name == "scan_completed":
            return DictEventMapper({"scan_completed": EventKind.CYCLE_COMPLETED}).map_event(event_name, payload)
        return None


def test_control_plane_facade_session_budget_and_replay(tmp_path: Path):
    db_file = tmp_path / "cp_facade.db"
    facade = ControlPlaneFacade.from_database_url(
        f"sqlite:///{db_file}",
        mapper=_SecurityMapper(),
        unknown_policy=UnknownAppEventPolicy.RAISE,
    )
    facade.setup()

    sid = facade.open_session("facade-demo", max_cost=Decimal("25"), max_action_count=3)
    assert facade.check_budget(sid, cost=Decimal("10"), action_count=1) is True
    facade.increment_budget(sid, cost=Decimal("10"), action_count=1)

    seq = facade.emit_app(sid, "scan_started", {"resource": "host-1"}, state_bearing=True, agent_id="sec-agent")
    assert seq == 1
    close_result = facade.close_session(sid)
    assert close_result.events_appended == 0
    assert close_result.session.status.value == "completed"

    events = facade.replay(sid)
    assert len(events) == 1
    assert events[0].event_kind == EventKind.CYCLE_STARTED
    assert events[0].state_bearing is True

    emitted = facade.emit(
        sid,
        EventKind.CYCLE_COMPLETED,
        {"done": True},
        state_bearing=True,
        agent_id="sec-agent",
    )
    assert emitted == 2

    sid2 = facade.open_session("abort-demo", max_cost=Decimal("5"), max_action_count=1)
    abort_result = facade.abort_session(sid2, reason="operator stop")
    assert abort_result.session.status.value == "aborted"
    facade.close()


def test_control_plane_facade_command_id_idempotency(tmp_path: Path):
    db_file = tmp_path / "cp_facade_idempotency.db"
    facade = ControlPlaneFacade.from_database_url(f"sqlite:///{db_file}")
    facade.setup()

    sid = facade.open_session("idempotency-demo", command_id="sync-open-1")
    sid_again = facade.open_session("ignored-name", command_id="sync-open-1")
    assert sid_again == sid

    seq1 = facade.emit(sid, EventKind.CYCLE_STARTED, {"phase": "one"}, command_id="sync-emit-1")
    seq2 = facade.emit(sid, EventKind.CYCLE_STARTED, {"phase": "two"}, command_id="sync-emit-1")
    assert seq2 == seq1

    close1 = facade.close_session(sid, command_id="sync-close-1")
    close2 = facade.close_session(sid, command_id="sync-close-1")
    assert close1.session.status.value == "completed"
    assert close2.session.status.value == "completed"

    sid_kill = facade.open_session("kill-target")
    kill1 = facade.kill_session(sid_kill, command_id="sync-kill-1")
    kill2 = facade.kill_session(sid_kill, command_id="sync-kill-1")
    assert kill1.scope == kill2.scope
    assert kill1.session_id == kill2.session_id
    assert kill1.tickets_denied == kill2.tickets_denied

    facade.close()
