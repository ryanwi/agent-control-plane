"""Minimal runnable sync quickstart for agent-control-plane.

Run:
    uv run python examples/quickstart_sync.py
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from agent_control_plane.sync import ControlPlaneFacade, DictEventMapper
from agent_control_plane.types.enums import EventKind


def main() -> None:
    Path("./control_plane_sync_example.db").unlink(missing_ok=True)
    mapper = DictEventMapper(
        {
            "job_started": EventKind.CYCLE_STARTED,
            "job_completed": EventKind.CYCLE_COMPLETED,
        }
    )
    cp = ControlPlaneFacade.from_database_url(
        "sqlite:///./control_plane_sync_example.db",
        mapper=mapper,
    )
    cp.setup()

    sid = cp.open_session("sync-demo", max_cost=Decimal("50"), max_action_count=10)
    print(f"Created session: {sid}")

    ok = cp.check_budget(sid, cost=Decimal("12.50"), action_count=1)
    print(f"Budget check (12.50): {ok}")

    cp.increment_budget(sid, cost=Decimal("12.50"), action_count=1)
    remaining = cp.get_remaining_budget(sid)
    print(f"Remaining: ${remaining['remaining_cost']} cost, {remaining['remaining_count']} actions")

    seq = cp.emit_app(sid, "job_started", {"job_id": "sync-demo-1"})
    print(f"App event appended at seq={seq}")

    cp.close_session(sid, payload={"result": "ok"})
    events = cp.replay(sid)
    print(f"Recorded {len(events)} events")
    cp.close()


if __name__ == "__main__":
    main()
