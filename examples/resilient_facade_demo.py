"""Resilient facade demo — compare before/after integration ceremony.

Run:
    uv run python examples/resilient_facade_demo.py
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from agent_control_plane import (
    ControlPlaneSetup,
    EventKind,
    ResilienceMode,
)


def main() -> None:
    Path("./resilient_demo.db").unlink(missing_ok=True)

    # ~10 lines replaces ~100 lines of bootstrap + wrapper code
    cp = ControlPlaneSetup(
        "sqlite:///./resilient_demo.db",
        event_map={
            "job_started": EventKind.CYCLE_STARTED,
            "job_completed": EventKind.CYCLE_COMPLETED,
        },
        action_names=["place_order", "cancel_order"],
        resilience_mode=ResilienceMode.MIXED,
    ).build()

    sid = cp.open_session("demo", max_cost=Decimal("500"), max_action_count=20)
    print(f"Session: {sid}")

    # Telemetry — fail-open in MIXED mode
    cp.emit(sid, EventKind.CYCLE_STARTED, {"cycle": 1})
    cp.emit_app(sid, "job_started", {"job_id": "demo-1"})

    # Budget check — fail-open (returns True on error)
    ok = cp.check_budget(sid, cost=Decimal("25"))
    print(f"Budget ok: {ok}")

    # Budget increment — fail-closed (state-bearing)
    cp.increment_budget(sid, cost=Decimal("25"))

    cp.close_session(sid)
    print(f"Events: {len(cp.replay(sid))}")
    cp.close()
    Path("./resilient_demo.db").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
