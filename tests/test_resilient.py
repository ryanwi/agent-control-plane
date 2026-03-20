"""Tests for ResilientControlPlane fail-open/fail-closed semantics."""

from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from agent_control_plane.resilient import ResilientControlPlane
from agent_control_plane.sync import ControlPlaneFacade, DictEventMapper
from agent_control_plane.types.enums import (
    EventKind,
    OperationCategory,
    ResilienceMode,
)


@pytest.fixture()
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path}/resilient_test.db"


@pytest.fixture()
def facade(db_url: str) -> ControlPlaneFacade:
    mapper = DictEventMapper({"job_started": EventKind.CYCLE_STARTED})
    f = ControlPlaneFacade.from_database_url(db_url, mapper=mapper)
    f.setup()
    return f


@pytest.fixture()
def rcp(facade: ControlPlaneFacade) -> ResilientControlPlane:
    return ResilientControlPlane(facade, mode=ResilienceMode.MIXED)


class TestMixedMode:
    """MIXED mode: state-bearing fails closed, telemetry/query/budget fail open."""

    def test_open_session_works(self, rcp: ResilientControlPlane) -> None:
        sid = rcp.open_session("test", max_cost=Decimal("100"))
        assert isinstance(sid, UUID)

    def test_emit_telemetry_works(self, rcp: ResilientControlPlane) -> None:
        sid = rcp.open_session("test")
        seq = rcp.emit(sid, EventKind.CYCLE_STARTED, {"x": 1})
        assert isinstance(seq, int)

    def test_check_budget_works(self, rcp: ResilientControlPlane) -> None:
        sid = rcp.open_session("test", max_cost=Decimal("100"))
        assert rcp.check_budget(sid, cost=Decimal("10")) is True

    def test_query_works(self, rcp: ResilientControlPlane) -> None:
        sid = rcp.open_session("test")
        session = rcp.get_session(sid)
        assert session is not None

    def test_close_session_works(self, rcp: ResilientControlPlane) -> None:
        sid = rcp.open_session("test")
        result = rcp.close_session(sid)
        assert result is not None

    def test_emit_app_works(self, rcp: ResilientControlPlane) -> None:
        sid = rcp.open_session("test")
        seq = rcp.emit_app(sid, "job_started", {"job_id": "1"})
        assert isinstance(seq, int)

    def test_replay_works(self, rcp: ResilientControlPlane) -> None:
        sid = rcp.open_session("test")
        rcp.emit(sid, EventKind.CYCLE_STARTED, {})
        events = rcp.replay(sid)
        assert len(events) >= 1


class TestFailOpen:
    """FAIL_OPEN mode: all errors return safe defaults."""

    def test_telemetry_returns_none_on_error(self, facade: ControlPlaneFacade) -> None:
        rcp = ResilientControlPlane(facade, mode=ResilienceMode.FAIL_OPEN)
        # Emit to a non-existent session
        result = rcp.emit(UUID(int=999), EventKind.CYCLE_STARTED, {})
        assert result is None

    def test_query_returns_none_on_error(self, facade: ControlPlaneFacade) -> None:
        rcp = ResilientControlPlane(facade, mode=ResilienceMode.FAIL_OPEN)
        # get_session for non-existent just returns None naturally,
        # so test replay on invalid session
        events = rcp.replay(UUID(int=999))
        # Should return empty list (the default) since the session doesn't exist
        assert isinstance(events, list)

    def test_budget_check_returns_true_on_error(self, facade: ControlPlaneFacade) -> None:
        rcp = ResilientControlPlane(facade, mode=ResilienceMode.FAIL_OPEN)
        result = rcp.check_budget(UUID(int=999))
        assert result is True

    def test_state_bearing_returns_default_on_error(self, facade: ControlPlaneFacade) -> None:
        rcp = ResilientControlPlane(facade, mode=ResilienceMode.FAIL_OPEN)
        # close_session on non-existent session
        result = rcp.close_session(UUID(int=999))
        assert result is None

    def test_open_session_returns_zero_uuid_on_error(self, db_url: str) -> None:
        # Use a broken facade to force an error
        bad_facade = ControlPlaneFacade.from_database_url("sqlite:///nonexistent/path/db.db")
        rcp = ResilientControlPlane(bad_facade, mode=ResilienceMode.FAIL_OPEN)
        result = rcp.open_session("test")
        assert result == UUID(int=0)


class TestFailClosed:
    """FAIL_CLOSED mode: all errors raise."""

    def test_telemetry_raises_on_error(self, facade: ControlPlaneFacade) -> None:
        rcp = ResilientControlPlane(facade, mode=ResilienceMode.FAIL_CLOSED)
        with pytest.raises((ValueError, OSError)):
            rcp.emit(UUID(int=999), EventKind.CYCLE_STARTED, {}, state_bearing=True)

    def test_budget_check_raises_on_error(self, facade: ControlPlaneFacade) -> None:
        rcp = ResilientControlPlane(facade, mode=ResilienceMode.FAIL_CLOSED)
        with pytest.raises((ValueError, OSError)):
            rcp.check_budget(UUID(int=999))

    def test_state_bearing_raises_on_error(self, facade: ControlPlaneFacade) -> None:
        rcp = ResilientControlPlane(facade, mode=ResilienceMode.FAIL_CLOSED)
        with pytest.raises((ValueError, OSError)):
            rcp.close_session(UUID(int=999))


class TestCategoryOverrides:
    """Per-category resilience mode overrides."""

    def test_override_budget_to_fail_closed(self, facade: ControlPlaneFacade) -> None:
        rcp = ResilientControlPlane(
            facade,
            mode=ResilienceMode.MIXED,
            category_overrides={OperationCategory.BUDGET: ResilienceMode.FAIL_CLOSED},
        )
        with pytest.raises((ValueError, OSError)):
            rcp.check_budget(UUID(int=999))

    def test_override_state_bearing_to_fail_open(self, facade: ControlPlaneFacade) -> None:
        rcp = ResilientControlPlane(
            facade,
            mode=ResilienceMode.MIXED,
            category_overrides={OperationCategory.STATE_BEARING: ResilienceMode.FAIL_OPEN},
        )
        result = rcp.close_session(UUID(int=999))
        assert result is None


class TestEmitStateBearingRespected:
    """emit() with state_bearing=True should use STATE_BEARING category."""

    def test_state_bearing_emit_raises_in_mixed(self, facade: ControlPlaneFacade) -> None:
        rcp = ResilientControlPlane(facade, mode=ResilienceMode.MIXED)
        with pytest.raises((ValueError, OSError)):
            rcp.emit(UUID(int=999), EventKind.CYCLE_STARTED, {}, state_bearing=True)

    def test_non_state_bearing_emit_returns_none_in_mixed(self, facade: ControlPlaneFacade) -> None:
        rcp = ResilientControlPlane(facade, mode=ResilienceMode.MIXED)
        result = rcp.emit(UUID(int=999), EventKind.CYCLE_STARTED, {})
        assert result is None


class TestLogging:
    """Verify warnings are logged on fail-open errors."""

    def test_logs_warning_on_fail_open(self, facade: ControlPlaneFacade, caplog: pytest.LogCaptureFixture) -> None:
        rcp = ResilientControlPlane(facade, mode=ResilienceMode.FAIL_OPEN)
        with caplog.at_level(logging.WARNING):
            rcp.check_budget(UUID(int=999))
        assert any("check_budget" in rec.message for rec in caplog.records)


class TestFacadeAccess:
    """The underlying facade is accessible for advanced use cases."""

    def test_facade_property(self, facade: ControlPlaneFacade) -> None:
        rcp = ResilientControlPlane(facade)
        assert rcp.facade is facade
