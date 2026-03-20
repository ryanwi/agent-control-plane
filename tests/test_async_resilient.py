"""Tests for AsyncResilientControlPlane fail-open/fail-closed semantics."""

from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from agent_control_plane.async_facade import AsyncControlPlaneFacade
from agent_control_plane.async_resilient import AsyncResilientControlPlane
from agent_control_plane.setup import ControlPlaneSetup
from agent_control_plane.sync import DictEventMapper
from agent_control_plane.types.enums import (
    EventKind,
    OperationCategory,
    ResilienceMode,
)


@pytest.fixture()
def db_url(tmp_path: Path) -> str:
    return f"sqlite+aiosqlite:///{tmp_path}/async_resilient_test.db"


@pytest.fixture()
async def facade(db_url: str) -> AsyncControlPlaneFacade:
    mapper = DictEventMapper({"job_started": EventKind.CYCLE_STARTED})
    f = AsyncControlPlaneFacade.from_database_url(db_url, mapper=mapper)
    yield f
    await f.close()


@pytest.fixture()
def rcp(facade: AsyncControlPlaneFacade) -> AsyncResilientControlPlane:
    return AsyncResilientControlPlane(facade, mode=ResilienceMode.MIXED)


class TestMixedMode:
    """MIXED mode: state-bearing fails closed, telemetry/query/budget fail open."""

    async def test_open_session_works(self, rcp: AsyncResilientControlPlane) -> None:
        sid = await rcp.open_session("test", max_cost=Decimal("100"))
        assert isinstance(sid, UUID)

    async def test_emit_telemetry_works(self, rcp: AsyncResilientControlPlane) -> None:
        sid = await rcp.open_session("test")
        seq = await rcp.emit(sid, EventKind.CYCLE_STARTED, {"x": 1})
        assert isinstance(seq, int)

    async def test_check_budget_works(self, rcp: AsyncResilientControlPlane) -> None:
        sid = await rcp.open_session("test", max_cost=Decimal("100"))
        assert await rcp.check_budget(sid, cost=Decimal("10")) is True

    async def test_query_works(self, rcp: AsyncResilientControlPlane) -> None:
        sid = await rcp.open_session("test")
        session = await rcp.get_session(sid)
        assert session is not None

    async def test_close_session_works(self, rcp: AsyncResilientControlPlane) -> None:
        sid = await rcp.open_session("test")
        result = await rcp.close_session(sid)
        assert result is not None

    async def test_emit_app_works(self, rcp: AsyncResilientControlPlane) -> None:
        sid = await rcp.open_session("test")
        seq = await rcp.emit_app(sid, "job_started", {"job_id": "1"})
        assert isinstance(seq, int)

    async def test_replay_works(self, rcp: AsyncResilientControlPlane) -> None:
        sid = await rcp.open_session("test")
        await rcp.emit(sid, EventKind.CYCLE_STARTED, {})
        events = await rcp.replay(sid)
        assert len(events) >= 1

    async def test_list_sessions_works(self, rcp: AsyncResilientControlPlane) -> None:
        await rcp.open_session("test")
        sessions = await rcp.list_sessions()
        assert len(sessions) >= 1

    async def test_activate_session_works(self, rcp: AsyncResilientControlPlane) -> None:
        sid = await rcp.open_session("test")
        result = await rcp.activate_session(sid)
        assert result is not None

    async def test_increment_budget_works(self, rcp: AsyncResilientControlPlane) -> None:
        sid = await rcp.open_session("test", max_cost=Decimal("100"))
        await rcp.increment_budget(sid, cost=Decimal("10"))
        remaining = await rcp.get_remaining_budget(sid)
        assert remaining is not None
        assert remaining["used_cost"] == Decimal("10")


class TestFailOpen:
    """FAIL_OPEN mode: all errors return safe defaults."""

    async def test_telemetry_returns_none_on_error(self, facade: AsyncControlPlaneFacade) -> None:
        rcp = AsyncResilientControlPlane(facade, mode=ResilienceMode.FAIL_OPEN)
        result = await rcp.emit(UUID(int=999), EventKind.CYCLE_STARTED, {})
        assert result is None

    async def test_query_returns_empty_list_on_error(self, facade: AsyncControlPlaneFacade) -> None:
        rcp = AsyncResilientControlPlane(facade, mode=ResilienceMode.FAIL_OPEN)
        events = await rcp.replay(UUID(int=999))
        assert isinstance(events, list)

    async def test_budget_check_returns_true_on_error(self, facade: AsyncControlPlaneFacade) -> None:
        rcp = AsyncResilientControlPlane(facade, mode=ResilienceMode.FAIL_OPEN)
        result = await rcp.check_budget(UUID(int=999))
        assert result is True

    async def test_state_bearing_returns_default_on_error(self, facade: AsyncControlPlaneFacade) -> None:
        rcp = AsyncResilientControlPlane(facade, mode=ResilienceMode.FAIL_OPEN)
        result = await rcp.close_session(UUID(int=999))
        assert result is None

    async def test_list_sessions_returns_empty_on_error(self, db_url: str) -> None:
        bad_facade = AsyncControlPlaneFacade.from_database_url("sqlite+aiosqlite:///nonexistent/path/db.db")
        rcp = AsyncResilientControlPlane(bad_facade, mode=ResilienceMode.FAIL_OPEN)
        result = await rcp.list_sessions()
        assert result == []

    async def test_recover_stuck_returns_default_on_error(self, db_url: str) -> None:
        bad_facade = AsyncControlPlaneFacade.from_database_url("sqlite+aiosqlite:///nonexistent/path/db.db")
        rcp = AsyncResilientControlPlane(bad_facade, mode=ResilienceMode.FAIL_OPEN)
        result = await rcp.recover_stuck_sessions()
        assert result == {"stuck_sessions": 0, "recovered": 0, "aborted": 0}


class TestFailClosed:
    """FAIL_CLOSED mode: all errors raise."""

    async def test_telemetry_raises_on_error(self, facade: AsyncControlPlaneFacade) -> None:
        rcp = AsyncResilientControlPlane(facade, mode=ResilienceMode.FAIL_CLOSED)
        with pytest.raises((ValueError, OSError)):
            await rcp.emit(UUID(int=999), EventKind.CYCLE_STARTED, {}, state_bearing=True)

    async def test_budget_check_raises_on_error(self, facade: AsyncControlPlaneFacade) -> None:
        rcp = AsyncResilientControlPlane(facade, mode=ResilienceMode.FAIL_CLOSED)
        with pytest.raises((ValueError, OSError)):
            await rcp.check_budget(UUID(int=999))

    async def test_state_bearing_raises_on_error(self, facade: AsyncControlPlaneFacade) -> None:
        rcp = AsyncResilientControlPlane(facade, mode=ResilienceMode.FAIL_CLOSED)
        with pytest.raises((ValueError, OSError)):
            await rcp.close_session(UUID(int=999))


class TestCategoryOverrides:
    """Per-category resilience mode overrides."""

    async def test_override_budget_to_fail_closed(self, facade: AsyncControlPlaneFacade) -> None:
        rcp = AsyncResilientControlPlane(
            facade,
            mode=ResilienceMode.MIXED,
            category_overrides={OperationCategory.BUDGET: ResilienceMode.FAIL_CLOSED},
        )
        with pytest.raises((ValueError, OSError)):
            await rcp.check_budget(UUID(int=999))

    async def test_override_state_bearing_to_fail_open(self, facade: AsyncControlPlaneFacade) -> None:
        rcp = AsyncResilientControlPlane(
            facade,
            mode=ResilienceMode.MIXED,
            category_overrides={OperationCategory.STATE_BEARING: ResilienceMode.FAIL_OPEN},
        )
        result = await rcp.close_session(UUID(int=999))
        assert result is None


class TestEmitStateBearingRespected:
    """emit() with state_bearing=True should use STATE_BEARING category."""

    async def test_state_bearing_emit_raises_in_mixed(self, facade: AsyncControlPlaneFacade) -> None:
        rcp = AsyncResilientControlPlane(facade, mode=ResilienceMode.MIXED)
        with pytest.raises((ValueError, OSError)):
            await rcp.emit(UUID(int=999), EventKind.CYCLE_STARTED, {}, state_bearing=True)

    async def test_non_state_bearing_emit_returns_none_in_mixed(self, facade: AsyncControlPlaneFacade) -> None:
        rcp = AsyncResilientControlPlane(facade, mode=ResilienceMode.MIXED)
        result = await rcp.emit(UUID(int=999), EventKind.CYCLE_STARTED, {})
        assert result is None


class TestLogging:
    """Verify warnings are logged on fail-open errors."""

    async def test_logs_warning_on_fail_open(
        self, facade: AsyncControlPlaneFacade, caplog: pytest.LogCaptureFixture
    ) -> None:
        rcp = AsyncResilientControlPlane(facade, mode=ResilienceMode.FAIL_OPEN)
        with caplog.at_level(logging.WARNING):
            await rcp.check_budget(UUID(int=999))
        assert any("check_budget" in rec.message for rec in caplog.records)


class TestFacadeAccess:
    """The underlying facade is accessible for advanced use cases."""

    def test_facade_property(self, facade: AsyncControlPlaneFacade) -> None:
        rcp = AsyncResilientControlPlane(facade)
        assert rcp.facade is facade


class TestCloseDelegate:
    """close() delegates to facade."""

    async def test_close(self, facade: AsyncControlPlaneFacade) -> None:
        rcp = AsyncResilientControlPlane(facade)
        await rcp.close()


class TestBuildAsync:
    """ControlPlaneSetup.build_async() returns AsyncResilientControlPlane."""

    async def test_build_async_returns_async_rcp(self, db_url: str) -> None:
        cp = ControlPlaneSetup(
            db_url,
            event_map={"job_started": EventKind.CYCLE_STARTED},
            resilience_mode=ResilienceMode.MIXED,
        ).build_async()
        assert isinstance(cp, AsyncResilientControlPlane)
        sid = await cp.open_session("test")
        assert isinstance(sid, UUID)
        await cp.close()

    async def test_build_async_fail_open(self, db_url: str) -> None:
        cp = ControlPlaneSetup(
            db_url,
            resilience_mode=ResilienceMode.FAIL_OPEN,
        ).build_async()
        result = await cp.check_budget(UUID(int=999))
        assert result is True
        await cp.close()
