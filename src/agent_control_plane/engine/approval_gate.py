"""Approval ticket lifecycle: create, wait, resolve, timeout."""

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agent_control_plane.engine.event_store import EventStore
from agent_control_plane.models.registry import ModelRegistry
from agent_control_plane.types.enums import (
    ApprovalDecisionType,
    ApprovalStatus,
    EventKind,
    ProposalStatus,
    RiskLevel,
)

logger = logging.getLogger(__name__)


class ApprovalGate:
    """Manages the approval ticket lifecycle for action proposals."""

    def __init__(self, event_store: EventStore) -> None:
        self.event_store = event_store

    async def create_ticket(
        self,
        db_session: AsyncSession,
        session_id: UUID,
        proposal_id: UUID,
        timeout_seconds: int = 3600,
    ) -> Any:
        """Create a pending approval ticket for a proposal.

        This is a state-bearing write -- failure aborts the operation.
        """
        ApprovalTicket = ModelRegistry.get("ApprovalTicket")
        timeout_at = datetime.now(UTC) + timedelta(seconds=timeout_seconds)
        ticket = ApprovalTicket(
            id=uuid4(),
            session_id=session_id,
            proposal_id=proposal_id,
            status=ApprovalStatus.PENDING,
            timeout_at=timeout_at,
        )
        db_session.add(ticket)
        await db_session.flush()

        await self.event_store.append(
            db_session,
            session_id=session_id,
            event_kind=EventKind.APPROVAL_REQUESTED,
            payload={
                "ticket_id": str(ticket.id),
                "proposal_id": str(proposal_id),
                "timeout_at": timeout_at.isoformat(),
            },
            state_bearing=True,
        )

        logger.info("Created approval ticket %s for proposal %s", ticket.id, proposal_id)
        return ticket

    async def approve(
        self,
        db_session: AsyncSession,
        ticket_id: UUID,
        *,
        decision_type: ApprovalDecisionType = ApprovalDecisionType.ALLOW_ONCE,
        decided_by: str = "operator",
        reason: str | None = None,
        scope_resource_ids: list[str] | None = None,
        scope_max_cost: Decimal | None = None,
        scope_max_count: int | None = None,
        scope_expiry: datetime | None = None,
    ) -> Any:
        """Approve a pending ticket. State-bearing write."""
        ticket = await self._get_pending_ticket(db_session, ticket_id)

        ticket.status = ApprovalStatus.APPROVED
        ticket.decision_type = decision_type
        ticket.decided_by = decided_by
        ticket.decision_reason = reason
        ticket.decided_at = datetime.now(UTC)

        # Set scope constraints for allow_for_session
        if decision_type == ApprovalDecisionType.ALLOW_FOR_SESSION:
            ticket.scope_resource_ids = scope_resource_ids
            ticket.scope_max_cost = scope_max_cost
            ticket.scope_max_count = scope_max_count
            ticket.scope_expiry = scope_expiry

        await db_session.flush()

        # Update proposal status
        ActionProposal = ModelRegistry.get("ActionProposal")
        await db_session.execute(
            update(ActionProposal).where(ActionProposal.id == ticket.proposal_id).values(status=ProposalStatus.APPROVED)
        )

        await self.event_store.append(
            db_session,
            session_id=ticket.session_id,
            event_kind=EventKind.APPROVAL_GRANTED,
            payload={
                "ticket_id": str(ticket_id),
                "proposal_id": str(ticket.proposal_id),
                "decision_type": decision_type,
                "decided_by": decided_by,
            },
            state_bearing=True,
        )

        logger.info("Approved ticket %s (%s)", ticket_id, decision_type)
        return ticket

    async def deny(
        self,
        db_session: AsyncSession,
        ticket_id: UUID,
        *,
        decided_by: str = "operator",
        reason: str | None = None,
    ) -> Any:
        """Deny a pending ticket. State-bearing write."""
        ticket = await self._get_pending_ticket(db_session, ticket_id)

        ticket.status = ApprovalStatus.DENIED
        ticket.decided_by = decided_by
        ticket.decision_reason = reason
        ticket.decided_at = datetime.now(UTC)
        await db_session.flush()

        # Update proposal status
        ActionProposal = ModelRegistry.get("ActionProposal")
        await db_session.execute(
            update(ActionProposal).where(ActionProposal.id == ticket.proposal_id).values(status=ProposalStatus.DENIED)
        )

        await self.event_store.append(
            db_session,
            session_id=ticket.session_id,
            event_kind=EventKind.APPROVAL_DENIED,
            payload={
                "ticket_id": str(ticket_id),
                "proposal_id": str(ticket.proposal_id),
                "decided_by": decided_by,
                "reason": reason,
            },
            state_bearing=True,
        )

        logger.info("Denied ticket %s", ticket_id)
        return ticket

    async def expire_timed_out_tickets(self, db_session: AsyncSession) -> int:
        """Find and expire all tickets past their timeout. Returns count expired."""
        ApprovalTicket = ModelRegistry.get("ApprovalTicket")
        ActionProposal = ModelRegistry.get("ActionProposal")
        now = datetime.now(UTC)
        result = await db_session.execute(
            select(ApprovalTicket).where(
                ApprovalTicket.status == ApprovalStatus.PENDING,
                ApprovalTicket.timeout_at <= now,
            )
        )
        tickets = list(result.scalars().all())

        for ticket in tickets:
            ticket.status = ApprovalStatus.EXPIRED
            ticket.decided_at = now
            ticket.decision_reason = "Timeout expired (safe default: deny)"

            # Update proposal status
            await db_session.execute(
                update(ActionProposal)
                .where(ActionProposal.id == ticket.proposal_id)
                .values(status=ProposalStatus.EXPIRED)
            )

            await self.event_store.append(
                db_session,
                session_id=ticket.session_id,
                event_kind=EventKind.APPROVAL_TIMEOUT,
                payload={
                    "ticket_id": str(ticket.id),
                    "proposal_id": str(ticket.proposal_id),
                },
                state_bearing=False,
            )

        if tickets:
            logger.info("Expired %d timed-out approval tickets", len(tickets))
        return len(tickets)

    async def check_session_scope(
        self,
        db_session: AsyncSession,
        session_id: UUID,
        resource_id: str,
        risk_level: RiskLevel = RiskLevel.MEDIUM,
        cost: Decimal = Decimal("0"),
    ) -> Any | None:
        """Check if an existing allow_for_session scope covers this proposal.

        Returns the matching ticket if scope applies, None otherwise.
        Critically: scope waives the human click, NOT the policy/risk checks.
        """
        ApprovalTicket = ModelRegistry.get("ApprovalTicket")
        is_sqlalchemy_model = hasattr(ApprovalTicket, "__table__") or hasattr(ApprovalTicket, "__mapper__")

        if is_sqlalchemy_model:
            result = await db_session.execute(
                select(ApprovalTicket)
                .where(
                    ApprovalTicket.session_id == session_id,
                    ApprovalTicket.status == ApprovalStatus.APPROVED,
                    ApprovalTicket.decision_type == ApprovalDecisionType.ALLOW_FOR_SESSION,
                )
                .with_for_update()
            )
            tickets = list(result.scalars().all())
        else:
            # Lightweight compatibility path for tests and non-SQLAlchemy adapters
            rows = getattr(db_session, "rows", [])
            tickets = [
                ticket
                for ticket in rows
                if getattr(ticket, "session_id", None) == session_id
                and ticket.status == ApprovalStatus.APPROVED
                and ticket.decision_type == ApprovalDecisionType.ALLOW_FOR_SESSION
            ]

        now = datetime.now(UTC)
        for ticket in tickets:
            # Check expiry
            if ticket.scope_expiry and ticket.scope_expiry <= now:
                continue
            # Check resource scope
            if ticket.scope_resource_ids and resource_id not in ticket.scope_resource_ids:
                continue
            # Check cost scope
            if ticket.scope_max_cost and cost > ticket.scope_max_cost:
                continue
            # Check count scope (stored as remaining approvals)
            if ticket.scope_max_count is not None:
                if ticket.scope_max_count <= 0:
                    continue
                ticket.scope_max_count -= 1
            # Scope matches
            await db_session.flush()
            return ticket

        return None

    async def get_pending_tickets(
        self,
        db_session: AsyncSession,
        *,
        session_id: UUID | None = None,
    ) -> list[Any]:
        """List pending approval tickets, optionally filtered by session."""
        ApprovalTicket = ModelRegistry.get("ApprovalTicket")
        query = select(ApprovalTicket).where(ApprovalTicket.status == ApprovalStatus.PENDING)
        if session_id:
            query = query.where(ApprovalTicket.session_id == session_id)
        query = query.order_by(ApprovalTicket.created_at.desc())
        result = await db_session.execute(query)
        return list(result.scalars().all())

    async def _get_pending_ticket(self, db_session: AsyncSession, ticket_id: UUID) -> Any:
        """Get a pending ticket or raise."""
        ApprovalTicket = ModelRegistry.get("ApprovalTicket")
        result = await db_session.execute(
            select(ApprovalTicket).where(ApprovalTicket.id == ticket_id).with_for_update()
        )
        ticket = result.scalar_one_or_none()
        if ticket is None:
            raise ValueError(f"Ticket {ticket_id} not found")
        if ticket.status != ApprovalStatus.PENDING:
            raise ValueError(f"Ticket {ticket_id} is not pending (status={ticket.status})")
        return ticket
