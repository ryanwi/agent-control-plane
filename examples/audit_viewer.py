"""
Audit Viewer Example: Formatted Flight Recorder for Control Sessions.

Demonstrates:
- Replaying the EventStore for a specific session.
- Formatting the audit trail into a readable timeline.
- Highlighting state-bearing transitions and routing decisions.
"""

import asyncio
import sys
from uuid import UUID

from sqlalchemy.ext.asyncio import create_async_engine

from agent_control_plane import (
    AsyncSqlAlchemyEventRepo,
    EventStore,
    register_models,
)

DATABASE_URL = "sqlite+aiosqlite:///./audit_example.db"


async def view_audit(session_id: UUID):
    register_models()
    engine = create_async_engine(DATABASE_URL)

    async with engine.connect() as conn:
        repo = AsyncSqlAlchemyEventRepo(conn)
        store = EventStore(repo)

        events = await store.replay(session_id)

        print(f"\n{'=' * 80}")
        print(f"AUDIT TRAIL FOR SESSION: {session_id}")
        print(f"{'=' * 80}\n")

        if not events:
            print("No events found for this session.")
            return

        for e in events:
            timestamp = e.created_at.strftime("%Y-%m-%d %H:%M:%S")
            state_marker = " [STATE] " if e.state_bearing else "         "
            print(f"{timestamp}{state_marker}{e.event_kind.upper():<25} | {e.payload}")

            if e.routing_decision:
                print(f"    └─ ROUTING: Tier={e.routing_decision.get('tier')} | Step={e.routing_step or 'unknown'}")
                print(f"    └─ REASON:  {e.routing_reason}")

        print(f"\n{'=' * 80}")
        print(f"END OF REPORT ({len(events)} events)")
        print(f"{'=' * 80}\n")

    await engine.dispose()


if __name__ == "__main__":
    # This example requires a valid session_id from one of the other examples
    # Usage: uv run python examples/audit_viewer.py <session_id>
    if len(sys.argv) < 2:
        print("Usage: uv run python examples/audit_viewer.py <session_id>")
        sys.exit(1)

    try:
        sid = UUID(sys.argv[1])
        asyncio.run(view_audit(sid))
    except ValueError:
        print("Invalid UUID format.")
