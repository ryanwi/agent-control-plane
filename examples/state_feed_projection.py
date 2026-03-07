"""State-change feed projection example.

Run:
    uv run python examples/state_feed_projection.py
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from agent_control_plane.async_facade import AsyncControlPlaneFacade
from agent_control_plane.models.registry import ModelRegistry
from agent_control_plane.types.enums import (
    ActionName,
    ActionTier,
    ApprovalStatus,
    EventKind,
    ProposalStatus,
    RiskLevel,
)


class InMemoryProjectionStore:
    """Tiny read-model store backed by dictionaries."""

    def __init__(self) -> None:
        self.last_cursor = 0
        self.tickets: dict[UUID, ApprovalStatus] = {}
        self.proposals: dict[UUID, ProposalStatus] = {}


async def sync_projection_once(
    facade: AsyncControlPlaneFacade,
    store: InMemoryProjectionStore,
    *,
    limit: int = 100,
) -> int:
    """Consume new state-bearing events and project canonical read models."""
    feed = await facade.get_state_change_feed(cursor=store.last_cursor, limit=limit)
    if not feed.items:
        return 0

    for item in feed.items:
        session_id = item.event.session_id
        ticket_page = await facade.list_tickets(session_id=session_id, limit=200, offset=0)
        for ticket in ticket_page.items:
            store.tickets[ticket.id] = ticket.status

        proposal_page = await facade.list_proposals(session_id=session_id, limit=200, offset=0)
        for proposal in proposal_page.items:
            store.proposals[proposal.id] = proposal.status

        store.last_cursor = item.cursor

    return len(feed.items)


async def main() -> None:
    db_path = Path("./projection_example.db")
    db_path.unlink(missing_ok=True)

    facade = AsyncControlPlaneFacade.from_database_url(f"sqlite+aiosqlite:///{db_path}")

    session_id = await facade.open_session("projection-demo")
    await facade.activate_session(session_id)

    async with facade.session_scope() as db:
        proposal_model = ModelRegistry.get("ActionProposal")
        proposal = proposal_model(
            id=uuid4(),
            session_id=session_id,
            cycle_event_seq=None,
            resource_id="lotwatch-asset-1",
            resource_type="asset",
            decision=ActionName.STATUS,
            reasoning="manual review needed",
            metadata_json={},
            weight=Decimal("1.0"),
            score=Decimal("0.9"),
            action_tier=ActionTier.ALWAYS_APPROVE,
            risk_level=RiskLevel.MEDIUM,
            status=ProposalStatus.PENDING,
        )
        db.add(proposal)
        await db.commit()
        proposal_id = proposal.id

    ticket = await facade.create_ticket(
        session_id,
        proposal_id,
        datetime.now(UTC) + timedelta(minutes=15),
    )
    await facade.emit(session_id, EventKind.CYCLE_STARTED, {"phase": "pre-approval"}, state_bearing=True)
    await facade.approve_ticket(ticket.id, reason="operator approved")
    await facade.emit(session_id, EventKind.CYCLE_COMPLETED, {"phase": "post-approval"}, state_bearing=True)

    projection = InMemoryProjectionStore()
    processed = await sync_projection_once(facade, projection)

    canonical_ticket = await facade.get_ticket(ticket.id)
    canonical_proposal = await facade.get_proposal(proposal_id)

    if canonical_ticket is None or canonical_proposal is None:
        raise RuntimeError("Canonical state missing")

    print(f"Processed feed events: {processed}")
    print(f"Projection cursor: {projection.last_cursor}")
    print(f"Projected ticket status: {projection.tickets[ticket.id]}")
    print(f"Projected proposal status: {projection.proposals[proposal_id]}")
    print(f"Canonical ticket status: {canonical_ticket.status}")
    print(f"Canonical proposal status: {canonical_proposal.status}")

    await facade.close()


if __name__ == "__main__":
    asyncio.run(main())
