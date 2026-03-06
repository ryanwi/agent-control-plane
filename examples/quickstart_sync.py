"""Minimal runnable sync quickstart for agent-control-plane.

Run:
    uv run python examples/quickstart_sync.py
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from agent_control_plane.sync import SyncControlPlane


def main() -> None:
    Path("./control_plane_sync_example.db").unlink(missing_ok=True)
    cp = SyncControlPlane("sqlite:///./control_plane_sync_example.db")
    cp.setup()

    sid = cp.create_session("sync-demo", max_cost=Decimal("50"), max_action_count=10)
    print(f"Created session: {sid}")

    ok = cp.check_budget(sid, cost=Decimal("12.50"), action_count=1)
    print(f"Budget check (12.50): {ok}")

    cp.increment_budget(sid, cost=Decimal("12.50"), action_count=1)
    remaining = cp.get_remaining_budget(sid)
    print(f"Remaining: ${remaining['remaining_cost']} cost, {remaining['remaining_count']} actions")

    halted = cp.kill(sid, reason="operator requested stop")
    print(f"Kill switch result: {halted}")
    cp.close()


if __name__ == "__main__":
    main()
