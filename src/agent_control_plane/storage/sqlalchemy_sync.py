"""Sync SQLAlchemy storage backend.

Implements the sync repository protocols using SQLAlchemy Session.
SQLite doesn't support FOR UPDATE, so this backend uses
BEGIN IMMEDIATE transactions instead (handled at the session level).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from agent_control_plane.engine.budget_tracker import BudgetExhaustedError
from agent_control_plane.models.registry import ModelRegistry
from agent_control_plane.types.approvals import ApprovalTicketDTO
from agent_control_plane.types.enums import ApprovalDecisionType, ApprovalStatus, ProposalStatus
from agent_control_plane.types.frames import EventFrame
from agent_control_plane.types.sessions import BudgetInfo, SessionState


class SyncSqlAlchemySessionRepo:
    """Sync SQLAlchemy implementation of SessionRepository."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_session(self, session_id: UUID) -> SessionState | None:
        ControlSession = ModelRegistry.get("ControlSession")
        result = self._session.execute(select(ControlSession).where(ControlSession.id == session_id))
        row = result.scalar_one_or_none()
        return self._to_dto(row) if row else None

    def get_session_for_update(self, session_id: UUID) -> SessionState:
        ControlSession = ModelRegistry.get("ControlSession")
        result = self._session.execute(select(ControlSession).where(ControlSession.id == session_id))
        row = result.scalar_one_or_none()
        if row is None:
            raise ValueError(f"Session {session_id} not found")
        return self._to_dto(row)

    def create_session(self, **kwargs: Any) -> SessionState:
        ControlSession = ModelRegistry.get("ControlSession")
        sid = uuid4()
        cs = ControlSession(id=sid, **kwargs)
        self._session.add(cs)
        self._session.flush()
        return self._to_dto(cs)

    def update_session(self, session_id: UUID, **fields: Any) -> None:
        ControlSession = ModelRegistry.get("ControlSession")
        self._session.execute(update(ControlSession).where(ControlSession.id == session_id).values(**fields))

    def set_active_cycle(self, session_id: UUID, cycle_id: UUID | None) -> None:
        ControlSession = ModelRegistry.get("ControlSession")
        self._session.execute(
            update(ControlSession)
            .where(ControlSession.id == session_id)
            .values(active_cycle_id=cycle_id, updated_at=datetime.now(UTC))
        )

    def list_sessions(self, statuses: list[str] | None = None, limit: int = 50) -> list[SessionState]:
        ControlSession = ModelRegistry.get("ControlSession")
        query = select(ControlSession).order_by(ControlSession.created_at.desc()).limit(limit)
        if statuses:
            query = query.where(ControlSession.status.in_(statuses))
        result = self._session.execute(query)
        return [self._to_dto(row) for row in result.scalars().all()]

    def increment_budget(self, session_id: UUID, cost: Decimal, action_count: int) -> None:
        ControlSession = ModelRegistry.get("ControlSession")
        result = self._session.execute(select(ControlSession).where(ControlSession.id == session_id))
        cs = result.scalar_one()
        new_cost = cs.used_cost + cost
        new_count = cs.used_action_count + action_count
        if new_cost > cs.max_cost:
            raise BudgetExhaustedError(f"Cost budget exceeded: {new_cost} > {cs.max_cost}")
        if new_count > cs.max_action_count:
            raise BudgetExhaustedError(f"Action count exceeded: {new_count} > {cs.max_action_count}")
        self._session.execute(
            update(ControlSession)
            .where(ControlSession.id == session_id)
            .values(used_cost=new_cost, used_action_count=new_count)
        )

    def get_budget(self, session_id: UUID) -> BudgetInfo:
        ControlSession = ModelRegistry.get("ControlSession")
        result = self._session.execute(select(ControlSession).where(ControlSession.id == session_id))
        cs = result.scalar_one_or_none()
        if cs is None:
            raise ValueError(f"Session {session_id} not found")
        return BudgetInfo(
            remaining_cost=cs.max_cost - cs.used_cost,
            remaining_count=cs.max_action_count - cs.used_action_count,
            used_cost=cs.used_cost,
            used_count=cs.used_action_count,
            max_cost=cs.max_cost,
            max_count=cs.max_action_count,
        )

    def create_policy(self, **kwargs: Any) -> UUID:
        PolicySnapshot = ModelRegistry.get("PolicySnapshot")
        pid = uuid4()
        policy = PolicySnapshot(id=pid, **kwargs)
        self._session.add(policy)
        self._session.flush()
        return pid

    def create_seq_counter(self, session_id: UUID) -> None:
        SessionSeqCounter = ModelRegistry.get("SessionSeqCounter")
        counter = SessionSeqCounter(id=uuid4(), session_id=session_id, next_seq=1)
        self._session.add(counter)
        self._session.flush()

    def _to_dto(self, row: Any) -> SessionState:
        return SessionState(
            id=row.id,
            session_name=row.session_name,
            status=row.status,
            execution_mode=row.execution_mode,
            asset_scope=getattr(row, "asset_scope", None),
            max_cost=row.max_cost,
            used_cost=row.used_cost,
            max_action_count=row.max_action_count,
            used_action_count=row.used_action_count,
            active_policy_id=getattr(row, "active_policy_id", None),
            active_cycle_id=getattr(row, "active_cycle_id", None),
            dry_run_session_id=getattr(row, "dry_run_session_id", None),
            abort_reason=getattr(row, "abort_reason", None),
            abort_details=getattr(row, "abort_details", None),
            created_at=row.created_at,
            updated_at=getattr(row, "updated_at", None),
        )


class SyncSqlAlchemyEventRepo:
    """Sync SQLAlchemy implementation of EventRepository."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def append(
        self,
        session_id: UUID,
        event_kind: str,
        payload: dict[str, Any],
        *,
        state_bearing: bool = False,
        agent_id: str | None = None,
        correlation_id: UUID | None = None,
        routing_decision: dict[str, Any] | None = None,
        routing_reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> int:
        seq = self._allocate_seq(session_id)
        ControlEvent = ModelRegistry.get("ControlEvent")
        event = ControlEvent(
            id=uuid4(),
            session_id=session_id,
            seq=seq,
            event_kind=event_kind,
            agent_id=agent_id,
            correlation_id=correlation_id,
            payload=payload,
            routing_decision=routing_decision,
            routing_reason=routing_reason,
            idempotency_key=idempotency_key,
        )
        self._session.add(event)
        self._session.flush()
        return seq

    def replay(self, session_id: UUID, after_seq: int = 0, limit: int = 100) -> list[EventFrame]:
        ControlEvent = ModelRegistry.get("ControlEvent")
        result = self._session.execute(
            select(ControlEvent)
            .where(ControlEvent.session_id == session_id, ControlEvent.seq > after_seq)
            .order_by(ControlEvent.seq)
            .limit(limit)
        )
        return [self._to_dto(row) for row in result.scalars().all()]

    def get_last_event(self, session_id: UUID) -> EventFrame | None:
        ControlEvent = ModelRegistry.get("ControlEvent")
        result = self._session.execute(
            select(ControlEvent).where(ControlEvent.session_id == session_id).order_by(ControlEvent.seq.desc()).limit(1)
        )
        row = result.scalar_one_or_none()
        return self._to_dto(row) if row else None

    def _allocate_seq(self, session_id: UUID) -> int:
        SessionSeqCounter = ModelRegistry.get("SessionSeqCounter")
        result = self._session.execute(select(SessionSeqCounter).where(SessionSeqCounter.session_id == session_id))
        counter = result.scalar_one_or_none()
        if counter is None:
            raise ValueError(f"No sequence counter for session {session_id}")
        allocated = counter.next_seq
        self._session.execute(
            update(SessionSeqCounter)
            .where(SessionSeqCounter.session_id == session_id)
            .values(next_seq=SessionSeqCounter.next_seq + 1)
        )
        return allocated

    def _to_dto(self, row: Any) -> EventFrame:
        return EventFrame(
            event_id=row.id,
            session_id=row.session_id,
            seq=row.seq,
            event_kind=row.event_kind,
            agent_id=row.agent_id,
            correlation_id=row.correlation_id,
            payload=row.payload,
            routing_decision=row.routing_decision,
            routing_reason=row.routing_reason,
            created_at=row.created_at,
        )


class SyncSqlAlchemyApprovalRepo:
    """Sync SQLAlchemy implementation of ApprovalRepository."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_ticket(self, session_id: UUID, proposal_id: UUID, timeout_at: datetime) -> ApprovalTicketDTO:
        ApprovalTicket = ModelRegistry.get("ApprovalTicket")
        ticket = ApprovalTicket(
            id=uuid4(),
            session_id=session_id,
            proposal_id=proposal_id,
            status=ApprovalStatus.PENDING,
            timeout_at=timeout_at,
        )
        self._session.add(ticket)
        self._session.flush()
        return self._to_dto(ticket)

    def get_pending_ticket_for_update(self, ticket_id: UUID) -> ApprovalTicketDTO:
        ApprovalTicket = ModelRegistry.get("ApprovalTicket")
        result = self._session.execute(select(ApprovalTicket).where(ApprovalTicket.id == ticket_id))
        ticket = result.scalar_one_or_none()
        if ticket is None:
            raise ValueError(f"Ticket {ticket_id} not found")
        if ticket.status != ApprovalStatus.PENDING:
            raise ValueError(f"Ticket {ticket_id} is not pending (status={ticket.status})")
        return self._to_dto(ticket)

    def update_ticket(self, ticket_id: UUID, **fields: Any) -> None:
        ApprovalTicket = ModelRegistry.get("ApprovalTicket")
        self._session.execute(update(ApprovalTicket).where(ApprovalTicket.id == ticket_id).values(**fields))

    def get_pending_tickets(self, session_id: UUID | None = None) -> list[ApprovalTicketDTO]:
        ApprovalTicket = ModelRegistry.get("ApprovalTicket")
        query = select(ApprovalTicket).where(ApprovalTicket.status == ApprovalStatus.PENDING)
        if session_id:
            query = query.where(ApprovalTicket.session_id == session_id)
        result = self._session.execute(query)
        return [self._to_dto(row) for row in result.scalars().all()]

    def get_session_scope_tickets(self, session_id: UUID) -> list[ApprovalTicketDTO]:
        ApprovalTicket = ModelRegistry.get("ApprovalTicket")
        result = self._session.execute(
            select(ApprovalTicket).where(
                ApprovalTicket.session_id == session_id,
                ApprovalTicket.status == ApprovalStatus.APPROVED,
                ApprovalTicket.decision_type == ApprovalDecisionType.ALLOW_FOR_SESSION,
            )
        )
        return [self._to_dto(row) for row in result.scalars().all()]

    def decrement_scope_count(self, ticket_id: UUID) -> None:
        ApprovalTicket = ModelRegistry.get("ApprovalTicket")
        self._session.execute(
            update(ApprovalTicket)
            .where(ApprovalTicket.id == ticket_id)
            .values(scope_max_count=ApprovalTicket.scope_max_count - 1)
        )

    def deny_all_pending(self, session_id: UUID) -> int:
        ApprovalTicket = ModelRegistry.get("ApprovalTicket")
        result = self._session.execute(
            update(ApprovalTicket)
            .where(
                ApprovalTicket.session_id == session_id,
                ApprovalTicket.status == ApprovalStatus.PENDING,
            )
            .values(status=ApprovalStatus.DENIED, decision_reason="Kill switch triggered")
            .returning(ApprovalTicket.id)
        )
        return len(list(result.scalars().all()))

    def expire_timed_out(self) -> list[ApprovalTicketDTO]:
        ApprovalTicket = ModelRegistry.get("ApprovalTicket")
        now = datetime.now(UTC)
        result = self._session.execute(
            select(ApprovalTicket).where(
                ApprovalTicket.status == ApprovalStatus.PENDING,
                ApprovalTicket.timeout_at <= now,
            )
        )
        tickets = list(result.scalars().all())
        dtos = []
        for ticket in tickets:
            ticket.status = ApprovalStatus.EXPIRED
            ticket.decided_at = now
            ticket.decision_reason = "Timeout expired (safe default: deny)"
            dtos.append(self._to_dto(ticket))
        return dtos

    def _to_dto(self, row: Any) -> ApprovalTicketDTO:
        return ApprovalTicketDTO(
            id=row.id,
            session_id=row.session_id,
            proposal_id=row.proposal_id,
            scope_resource_ids=getattr(row, "scope_resource_ids", None),
            scope_max_cost=getattr(row, "scope_max_cost", None),
            scope_max_count=getattr(row, "scope_max_count", None),
            scope_expiry=getattr(row, "scope_expiry", None),
            status=row.status,
            decision_type=getattr(row, "decision_type", None),
            decided_by=getattr(row, "decided_by", None),
            decision_reason=getattr(row, "decision_reason", None),
            timeout_at=getattr(row, "timeout_at", None),
            created_at=row.created_at,
            decided_at=getattr(row, "decided_at", None),
        )


class SyncSqlAlchemyProposalRepo:
    """Sync SQLAlchemy implementation of ProposalRepository."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def update_status(self, proposal_id: UUID, status: str) -> None:
        ActionProposal = ModelRegistry.get("ActionProposal")
        self._session.execute(update(ActionProposal).where(ActionProposal.id == proposal_id).values(status=status))

    def has_pending_for_resource(self, session_id: UUID, resource_id: str) -> bool:
        ActionProposal = ModelRegistry.get("ActionProposal")
        result = self._session.execute(
            select(ActionProposal)
            .where(
                ActionProposal.session_id == session_id,
                ActionProposal.resource_id == resource_id,
                ActionProposal.status == ProposalStatus.PENDING,
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None


class SyncSqlAlchemyUnitOfWork:
    """Wraps a single sync Session, exposing all 4 repos."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self.session_repo = SyncSqlAlchemySessionRepo(session)
        self.event_repo = SyncSqlAlchemyEventRepo(session)
        self.approval_repo = SyncSqlAlchemyApprovalRepo(session)
        self.proposal_repo = SyncSqlAlchemyProposalRepo(session)

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()
