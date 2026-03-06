"""Tests for AsyncControlPlaneFacade."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from agent_control_plane.async_facade import AsyncControlPlaneFacade
from agent_control_plane.sync import DictEventMapper
from agent_control_plane.types.enums import EventKind, UnknownAppEventPolicy


@pytest.mark.asyncio
async def test_async_facade_session_budget_emit_and_close(tmp_path: Path):
    db_file = tmp_path / "cp_async_facade.db"
    facade = AsyncControlPlaneFacade.from_database_url(
        f"sqlite+aiosqlite:///{db_file}",
        mapper=DictEventMapper({"started": EventKind.CYCLE_STARTED}),
        unknown_policy=UnknownAppEventPolicy.RAISE,
    )

    sid = await facade.open_session("async-demo", max_cost=Decimal("20"), max_action_count=2)
    assert await facade.check_budget(sid, cost=Decimal("5"), action_count=1) is True
    await facade.increment_budget(sid, cost=Decimal("5"), action_count=1)

    seq = await facade.emit_app(sid, "started", {"k": "v"}, state_bearing=True, agent_id="agent-a")
    assert seq == 1

    close_result = await facade.close_session(sid)
    assert close_result.session.status.value == "completed"
    assert close_result.events_appended == 0

    events = await facade.replay(sid)
    assert len(events) == 1
    assert events[0].state_bearing is True

    await facade.close()
