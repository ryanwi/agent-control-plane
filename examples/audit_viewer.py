"""
Audit Viewer: list recent runs or replay the event trail for a specific session.

Usage:
    # list the 20 most recent sessions
    uv run python examples/audit_viewer.py

    # show the full event trail for one session
    uv run python examples/audit_viewer.py <session_id>
"""

from __future__ import annotations

import asyncio
import sys
from uuid import UUID

from agent_control_plane import AsyncControlPlaneFacade

DATABASE_URL = "sqlite+aiosqlite:///./control_plane.db"


async def list_recent(facade: AsyncControlPlaneFacade, limit: int = 20) -> None:
    sessions = await facade.observer.list_sessions(limit=limit)
    if not sessions:
        print("No sessions found.")
        return

    header = f"{'ID':<36}  {'NAME':<30}  {'STATUS':<10}  {'COST':>8}  {'DURATION':>10}"
    print(f"\n{header}")
    print("-" * len(header))

    for s in sessions:
        if s.started_at and s.updated_at:
            secs = (s.updated_at - s.started_at).total_seconds()
            duration = f"{secs:.1f}s"
        elif s.started_at:
            duration = "running"
        else:
            duration = "—"

        print(f"{s.id!s:<36}  {s.session_name:<30}  {s.status.value:<10}  {float(s.used_cost):>8.4f}  {duration:>10}")
    print()


async def show_trail(facade: AsyncControlPlaneFacade, session_id: UUID) -> None:
    session = await facade.sessions.get_session(session_id)
    if session is None:
        print(f"Session {session_id} not found.")
        return

    events = await facade.sessions.replay(session_id)

    print(f"\n{'=' * 80}")
    print(f"SESSION: {session.session_name}  ({session_id})")
    print(
        f"  status={session.status.value}  mode={session.execution_mode.value}"
        f"  cost={session.used_cost}/{session.max_cost}"
        f"  actions={session.used_action_count}/{session.max_action_count}"
    )
    if session.started_at:
        print(f"  started={session.started_at.strftime('%Y-%m-%d %H:%M:%S')}", end="")
        if session.updated_at:
            secs = (session.updated_at - session.started_at).total_seconds()
            print(f"  duration={secs:.1f}s", end="")
        print()
    print(f"{'=' * 80}\n")

    if not events:
        print("No events recorded.")
        return

    for e in events:
        ts = e.created_at.strftime("%H:%M:%S")
        marker = " [STATE]" if e.state_bearing else "        "
        agent = f" agent={e.agent_id}" if e.agent_id else ""
        print(f"  {ts}{marker}  seq={e.seq:<4}  {e.kind.value}{agent}")
        if e.payload:
            print(f"           {e.payload}")

    print(f"\n{len(events)} event(s)\n")


async def main() -> None:
    facade = AsyncControlPlaneFacade.from_database_url(DATABASE_URL)
    try:
        if len(sys.argv) < 2:
            await list_recent(facade)
        else:
            try:
                sid = UUID(sys.argv[1])
            except ValueError:
                print(f"Invalid UUID: {sys.argv[1]!r}")
                sys.exit(1)
            await show_trail(facade, sid)
    finally:
        await facade.close()


if __name__ == "__main__":
    asyncio.run(main())
