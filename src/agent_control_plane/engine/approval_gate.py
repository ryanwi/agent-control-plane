"""Approval ticket lifecycle: create, wait, resolve, timeout."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from agent_control_plane.engine.event_store import EventStore
from agent_control_plane.types.approvals import ApprovalTicketDTO
from agent_control_plane.types.enums import (
    ApprovalDecisionType,
    ApprovalStatus,
    EventKind,
    ProposalStatus,
)

if TYPE_CHECKING:
    from agent_control_plane.storage.protocols import AsyncApprovalRepository, AsyncProposalRepository

logger = logging.getLogger(__name__)


class ApprovalGate:
    """Manages the approval ticket lifecycle for action proposals."""

    def __init__(
        self,
        event_store: EventStore,
        approval_repo: AsyncApprovalRepository,
        proposal_repo: AsyncProposalRepository,
    ) -> None:
        self.event_store = event_store
        self._approval_repo = approval_repo
        self._proposal_repo = proposal_repo

    async def create_ticket(
        self,
        session_id: UUID,
        proposal_id: UUID,
        timeout_seconds: int = 3600,
    ) -> ApprovalTicketDTO:
        """Create a pending approval ticket for a proposal.

        This is a state-bearing write -- failure aborts the operation.
        """
        timeout_at = datetime.now(UTC) + timedelta(seconds=timeout_seconds)
        ticket = await self._approval_repo.create_ticket(session_id, proposal_id, timeout_at)

        await self.event_store.append(
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
        ticket_id: UUID,
        *,
        decision_type: ApprovalDecisionType = ApprovalDecisionType.ALLOW_ONCE,
        decided_by: str = "operator",
        reason: str | None = None,
        scope_resource_ids: list[str] | None = None,
        scope_max_cost: Decimal | None = None,
        scope_max_count: int | None = None,
        scope_expiry: datetime | None = None,
    ) -> ApprovalTicketDTO:
        """Approve a pending ticket. State-bearing write."""
        ticket = await self._approval_repo.get_pending_ticket_for_update(ticket_id)

        fields: dict[str, Any] = {
            "status": ApprovalStatus.APPROVED,
            "decision_type": decision_type,
            "decided_by": decided_by,
            "decision_reason": reason,
            "decided_at": datetime.now(UTC),
        }
        if decision_type == ApprovalDecisionType.ALLOW_FOR_SESSION:
            fields["scope_resource_ids"] = scope_resource_ids
            fields["scope_max_cost"] = scope_max_cost
            fields["scope_max_count"] = scope_max_count
            fields["scope_expiry"] = scope_expiry

        await self._approval_repo.update_ticket(ticket_id, **fields)
        await self._proposal_repo.update_status(ticket.proposal_id, ProposalStatus.APPROVED)

        await self.event_store.append(
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
        # Return updated ticket
        ticket.status = ApprovalStatus.APPROVED
        ticket.decision_type = decision_type
        return ticket

    async def deny(
        self,
        ticket_id: UUID,
        *,
        decided_by: str = "operator",
        reason: str | None = None,
    ) -> ApprovalTicketDTO:
        """Deny a pending ticket. State-bearing write."""
        ticket = await self._approval_repo.get_pending_ticket_for_update(ticket_id)

        await self._approval_repo.update_ticket(
            ticket_id,
            status=ApprovalStatus.DENIED,
            decided_by=decided_by,
            decision_reason=reason,
            decided_at=datetime.now(UTC),
        )
        await self._proposal_repo.update_status(ticket.proposal_id, ProposalStatus.DENIED)

        await self.event_store.append(
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
        ticket.status = ApprovalStatus.DENIED
        return ticket

    async def expire_timed_out_tickets(self) -> int:
        """Find and expire all tickets past their timeout. Returns count expired."""
        expired_tickets = await self._approval_repo.expire_timed_out()

        for ticket in expired_tickets:
            await self._proposal_repo.update_status(ticket.proposal_id, ProposalStatus.EXPIRED)
            await self.event_store.append(
                session_id=ticket.session_id,
                event_kind=EventKind.APPROVAL_TIMEOUT,
                payload={
                    "ticket_id": str(ticket.id),
                    "proposal_id": str(ticket.proposal_id),
                },
                state_bearing=False,
            )

        if expired_tickets:
            logger.info("Expired %d timed-out approval tickets", len(expired_tickets))
        return len(expired_tickets)

    async def check_session_scope(
        self,
        session_id: UUID,
        resource_id: str,
        cost: Decimal = Decimal("0"),
    ) -> ApprovalTicketDTO | None:
        """Check if an existing allow_for_session scope covers this proposal.

        Returns the matching ticket if scope applies, None otherwise.
        Critically: scope waives the human click, NOT the policy/risk checks.
        """
        tickets = await self._approval_repo.get_session_scope_tickets(session_id)

        now = datetime.now(UTC)
        for ticket in tickets:
            if ticket.scope_expiry and ticket.scope_expiry <= now:
                continue
            if ticket.scope_resource_ids and resource_id not in ticket.scope_resource_ids:
                continue
            if ticket.scope_max_cost and cost > ticket.scope_max_cost:
                continue
            if ticket.scope_max_count is not None:
                if ticket.scope_max_count <= 0:
                    continue
                await self._approval_repo.decrement_scope_count(ticket.id)
            return ticket

        return None

    async def get_pending_tickets(
        self,
        *,
        session_id: UUID | None = None,
    ) -> list[ApprovalTicketDTO]:
        """List pending approval tickets, optionally filtered by session."""
        return await self._approval_repo.get_pending_tickets(session_id=session_id)
