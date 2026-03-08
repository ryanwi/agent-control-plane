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
from agent_control_plane.types.agents import AgentCapability, AgentMetadata, DelegationProposal
from agent_control_plane.types.approvals import ApprovalTicketDTO
from agent_control_plane.types.enums import (
    ApprovalDecisionType,
    ApprovalStatus,
    EventKind,
    ProposalStatus,
    SessionStatus,
)
from agent_control_plane.types.frames import EventFrame
from agent_control_plane.types.proposals import ActionProposalDTO
from agent_control_plane.types.query import CommandResultDTO
from agent_control_plane.types.sessions import BudgetInfo, SessionState


class SyncSqlAlchemySessionRepo:
    """Sync SQLAlchemy implementation of SessionRepository."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_session(self, session_id: UUID) -> SessionState | None:
        control_session_model = ModelRegistry.get("ControlSession")
        result = self._session.execute(select(control_session_model).where(control_session_model.id == session_id))
        row = result.scalar_one_or_none()
        return self._to_dto(row) if row else None

    def get_session_for_update(self, session_id: UUID) -> SessionState:
        control_session_model = ModelRegistry.get("ControlSession")
        result = self._session.execute(select(control_session_model).where(control_session_model.id == session_id))
        row = result.scalar_one_or_none()
        if row is None:
            raise ValueError(f"Session {session_id} not found")
        return self._to_dto(row)

    def create_session(self, **kwargs: Any) -> SessionState:
        control_session_model = ModelRegistry.get("ControlSession")
        sid = uuid4()
        cs = control_session_model(id=sid, **kwargs)
        self._session.add(cs)
        self._session.flush()
        return self._to_dto(cs)

    def update_session(self, session_id: UUID, **fields: Any) -> None:
        control_session_model = ModelRegistry.get("ControlSession")
        self._session.execute(
            update(control_session_model).where(control_session_model.id == session_id).values(**fields)
        )

    def set_active_cycle(self, session_id: UUID, cycle_id: UUID | None) -> None:
        control_session_model = ModelRegistry.get("ControlSession")
        self._session.execute(
            update(control_session_model)
            .where(control_session_model.id == session_id)
            .values(active_cycle_id=cycle_id, updated_at=datetime.now(UTC))
        )

    def list_sessions(self, statuses: list[SessionStatus] | None = None, limit: int = 50) -> list[SessionState]:
        control_session_model = ModelRegistry.get("ControlSession")
        query = select(control_session_model).order_by(control_session_model.created_at.desc()).limit(limit)
        if statuses:
            query = query.where(control_session_model.status.in_(statuses))
        result = self._session.execute(query)
        return [self._to_dto(row) for row in result.scalars().all()]

    def increment_budget(self, session_id: UUID, cost: Decimal, action_count: int) -> None:
        control_session_model = ModelRegistry.get("ControlSession")
        result = self._session.execute(select(control_session_model).where(control_session_model.id == session_id))
        cs = result.scalar_one()
        new_cost = cs.used_cost + cost
        new_count = cs.used_action_count + action_count
        if new_cost > cs.max_cost:
            raise BudgetExhaustedError(f"Cost budget exceeded: {new_cost} > {cs.max_cost}")
        if new_count > cs.max_action_count:
            raise BudgetExhaustedError(f"Action count exceeded: {new_count} > {cs.max_action_count}")
        self._session.execute(
            update(control_session_model)
            .where(control_session_model.id == session_id)
            .values(used_cost=new_cost, used_action_count=new_count)
        )

    def get_budget(self, session_id: UUID) -> BudgetInfo:
        control_session_model = ModelRegistry.get("ControlSession")
        result = self._session.execute(select(control_session_model).where(control_session_model.id == session_id))
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
        policy_snapshot_model = ModelRegistry.get("PolicySnapshot")
        pid = uuid4()
        policy = policy_snapshot_model(id=pid, **kwargs)
        self._session.add(policy)
        self._session.flush()
        return pid

    def create_seq_counter(self, session_id: UUID) -> None:
        session_seq_counter_model = ModelRegistry.get("SessionSeqCounter")
        counter = session_seq_counter_model(id=uuid4(), session_id=session_id, next_seq=1)
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
        event_kind: EventKind,
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
        control_event_model = ModelRegistry.get("ControlEvent")
        event = control_event_model(
            id=uuid4(),
            session_id=session_id,
            seq=seq,
            event_kind=event_kind,
            agent_id=agent_id,
            correlation_id=correlation_id,
            payload=payload,
            state_bearing=state_bearing,
            routing_decision=routing_decision,
            routing_reason=routing_reason,
            idempotency_key=idempotency_key,
        )
        self._session.add(event)
        self._session.flush()
        return seq

    def replay(self, session_id: UUID, after_seq: int = 0, limit: int = 100) -> list[EventFrame]:
        control_event_model = ModelRegistry.get("ControlEvent")
        result = self._session.execute(
            select(control_event_model)
            .where(control_event_model.session_id == session_id, control_event_model.seq > after_seq)
            .order_by(control_event_model.seq)
            .limit(limit)
        )
        return [self._to_dto(row) for row in result.scalars().all()]

    def get_last_event(self, session_id: UUID) -> EventFrame | None:
        control_event_model = ModelRegistry.get("ControlEvent")
        result = self._session.execute(
            select(control_event_model)
            .where(control_event_model.session_id == session_id)
            .order_by(control_event_model.seq.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        return self._to_dto(row) if row else None

    def list_state_bearing_events(
        self, *, session_id: UUID | None = None, limit: int = 100, offset: int = 0
    ) -> list[EventFrame]:
        control_event_model = ModelRegistry.get("ControlEvent")
        query = (
            select(control_event_model)
            .where(control_event_model.state_bearing.is_(True))
            .order_by(control_event_model.created_at.asc(), control_event_model.seq.asc())
            .offset(offset)
            .limit(limit)
        )
        if session_id is not None:
            query = query.where(control_event_model.session_id == session_id)
        result = self._session.execute(query)
        return [self._to_dto(row) for row in result.scalars().all()]

    def _allocate_seq(self, session_id: UUID) -> int:
        session_seq_counter_model = ModelRegistry.get("SessionSeqCounter")
        result = self._session.execute(
            select(session_seq_counter_model).where(session_seq_counter_model.session_id == session_id)
        )
        counter = result.scalar_one_or_none()
        if counter is None:
            raise ValueError(f"No sequence counter for session {session_id}")
        allocated = counter.next_seq
        self._session.execute(
            update(session_seq_counter_model)
            .where(session_seq_counter_model.session_id == session_id)
            .values(next_seq=session_seq_counter_model.next_seq + 1)
        )
        return int(allocated)

    def _to_dto(self, row: Any) -> EventFrame:
        return EventFrame(
            event_id=row.id,
            session_id=row.session_id,
            seq=row.seq,
            event_kind=row.event_kind,
            agent_id=row.agent_id,
            correlation_id=row.correlation_id,
            payload=row.payload,
            state_bearing=getattr(row, "state_bearing", False),
            routing_decision=row.routing_decision,
            routing_reason=row.routing_reason,
            created_at=row.created_at,
        )


class SyncSqlAlchemyApprovalRepo:
    """Sync SQLAlchemy implementation of ApprovalRepository."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_ticket(self, session_id: UUID, proposal_id: UUID, timeout_at: datetime) -> ApprovalTicketDTO:
        approval_ticket_model = ModelRegistry.get("ApprovalTicket")
        ticket = approval_ticket_model(
            id=uuid4(),
            session_id=session_id,
            proposal_id=proposal_id,
            status=ApprovalStatus.PENDING,
            timeout_at=timeout_at,
        )
        self._session.add(ticket)
        self._session.flush()
        return self._to_dto(ticket)

    def get_ticket(self, ticket_id: UUID) -> ApprovalTicketDTO | None:
        approval_ticket_model = ModelRegistry.get("ApprovalTicket")
        result = self._session.execute(select(approval_ticket_model).where(approval_ticket_model.id == ticket_id))
        row = result.scalar_one_or_none()
        return self._to_dto(row) if row else None

    def get_pending_ticket_for_update(self, ticket_id: UUID) -> ApprovalTicketDTO:
        approval_ticket_model = ModelRegistry.get("ApprovalTicket")
        result = self._session.execute(select(approval_ticket_model).where(approval_ticket_model.id == ticket_id))
        ticket = result.scalar_one_or_none()
        if ticket is None:
            raise ValueError(f"Ticket {ticket_id} not found")
        if ticket.status != ApprovalStatus.PENDING:
            raise ValueError(f"Ticket {ticket_id} is not pending (status={ticket.status})")
        return self._to_dto(ticket)

    def update_ticket(self, ticket_id: UUID, **fields: Any) -> None:
        approval_ticket_model = ModelRegistry.get("ApprovalTicket")
        self._session.execute(
            update(approval_ticket_model).where(approval_ticket_model.id == ticket_id).values(**fields)
        )

    def get_pending_tickets(self, session_id: UUID | None = None) -> list[ApprovalTicketDTO]:
        approval_ticket_model = ModelRegistry.get("ApprovalTicket")
        query = select(approval_ticket_model).where(approval_ticket_model.status == ApprovalStatus.PENDING)
        if session_id:
            query = query.where(approval_ticket_model.session_id == session_id)
        result = self._session.execute(query)
        return [self._to_dto(row) for row in result.scalars().all()]

    def list_tickets(
        self,
        *,
        session_id: UUID | None = None,
        statuses: list[ApprovalStatus] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ApprovalTicketDTO]:
        approval_ticket_model = ModelRegistry.get("ApprovalTicket")
        query = select(approval_ticket_model)
        if session_id is not None:
            query = query.where(approval_ticket_model.session_id == session_id)
        if statuses:
            query = query.where(approval_ticket_model.status.in_(statuses))
        query = query.order_by(approval_ticket_model.created_at.desc()).offset(offset).limit(limit)
        result = self._session.execute(query)
        return [self._to_dto(row) for row in result.scalars().all()]

    def get_session_scope_tickets(self, session_id: UUID) -> list[ApprovalTicketDTO]:
        approval_ticket_model = ModelRegistry.get("ApprovalTicket")
        result = self._session.execute(
            select(approval_ticket_model).where(
                approval_ticket_model.session_id == session_id,
                approval_ticket_model.status == ApprovalStatus.APPROVED,
                approval_ticket_model.decision_type == ApprovalDecisionType.ALLOW_FOR_SESSION,
            )
        )
        return [self._to_dto(row) for row in result.scalars().all()]

    def decrement_scope_count(self, ticket_id: UUID) -> None:
        approval_ticket_model = ModelRegistry.get("ApprovalTicket")
        self._session.execute(
            update(approval_ticket_model)
            .where(approval_ticket_model.id == ticket_id)
            .values(scope_max_count=approval_ticket_model.scope_max_count - 1)
        )

    def deny_all_pending(self, session_id: UUID) -> int:
        approval_ticket_model = ModelRegistry.get("ApprovalTicket")
        result = self._session.execute(
            update(approval_ticket_model)
            .where(
                approval_ticket_model.session_id == session_id,
                approval_ticket_model.status == ApprovalStatus.PENDING,
            )
            .values(status=ApprovalStatus.DENIED, decision_reason="Kill switch triggered")
            .returning(approval_ticket_model.id)
        )
        return len(list(result.scalars().all()))

    def expire_timed_out(self) -> list[ApprovalTicketDTO]:
        approval_ticket_model = ModelRegistry.get("ApprovalTicket")
        from sqlalchemy.sql import func

        now = datetime.now(UTC)
        result = self._session.execute(
            select(approval_ticket_model).where(
                approval_ticket_model.status == ApprovalStatus.PENDING,
                approval_ticket_model.timeout_at <= func.now(),
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

    def create_proposal(self, proposal: ActionProposalDTO) -> ActionProposalDTO:
        action_proposal_model = ModelRegistry.get("ActionProposal")
        row = action_proposal_model(
            id=proposal.id,
            session_id=proposal.session_id,
            cycle_event_seq=proposal.cycle_event_seq,
            resource_id=proposal.resource_id,
            resource_type=proposal.resource_type,
            decision=proposal.decision,
            reasoning=proposal.reasoning,
            metadata_json=proposal.metadata,
            weight=proposal.weight,
            score=proposal.score,
            action_tier=proposal.action_tier,
            risk_level=proposal.risk_level,
            status=proposal.status,
            created_at=proposal.created_at,
        )
        self._session.add(row)
        self._session.flush()
        return self._to_dto(row)

    def get_proposal(self, proposal_id: UUID) -> ActionProposalDTO | None:
        action_proposal_model = ModelRegistry.get("ActionProposal")
        result = self._session.execute(select(action_proposal_model).where(action_proposal_model.id == proposal_id))
        row = result.scalar_one_or_none()
        return self._to_dto(row) if row else None

    def list_proposals(
        self,
        *,
        session_id: UUID | None = None,
        statuses: list[ProposalStatus] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ActionProposalDTO]:
        action_proposal_model = ModelRegistry.get("ActionProposal")
        query = select(action_proposal_model)
        if session_id is not None:
            query = query.where(action_proposal_model.session_id == session_id)
        if statuses:
            query = query.where(action_proposal_model.status.in_(statuses))
        query = query.order_by(action_proposal_model.created_at.desc()).offset(offset).limit(limit)
        result = self._session.execute(query)
        return [self._to_dto(row) for row in result.scalars().all()]

    def update_status(self, proposal_id: UUID, status: ProposalStatus) -> None:
        action_proposal_model = ModelRegistry.get("ActionProposal")
        self._session.execute(
            update(action_proposal_model).where(action_proposal_model.id == proposal_id).values(status=status)
        )

    def has_pending_for_resource(self, session_id: UUID, resource_id: str) -> bool:
        action_proposal_model = ModelRegistry.get("ActionProposal")
        result = self._session.execute(
            select(action_proposal_model)
            .where(
                action_proposal_model.session_id == session_id,
                action_proposal_model.resource_id == resource_id,
                action_proposal_model.status == ProposalStatus.PENDING,
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    def _to_dto(self, row: Any) -> ActionProposalDTO:
        return ActionProposalDTO(
            id=row.id,
            session_id=row.session_id,
            agent_id=getattr(row, "agent_id", None),
            cycle_event_seq=getattr(row, "cycle_event_seq", None),
            resource_id=row.resource_id,
            resource_type=row.resource_type,
            decision=row.decision,
            reasoning=row.reasoning,
            metadata=getattr(row, "metadata_json", {}) or {},
            weight=row.weight,
            score=row.score,
            risk_factors=getattr(row, "risk_factors", []) or [],
            supporting_signals=getattr(row, "supporting_signals", []) or [],
            action_tier=row.action_tier,
            risk_level=row.risk_level,
            status=row.status,
            created_at=row.created_at,
        )


class SyncSqlAlchemyCommandRepo:
    """Sync SQLAlchemy implementation of CommandRepository."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_command(self, command_id: str) -> CommandResultDTO | None:
        command_ledger_model = ModelRegistry.get("CommandLedger")
        result = self._session.execute(
            select(command_ledger_model).where(command_ledger_model.command_id == command_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return CommandResultDTO(
            command_id=row.command_id,
            operation=row.operation,
            result=row.result_json,
            created_at=row.created_at,
            session_id=getattr(row, "session_id", None),
        )

    def record_command(
        self,
        command_id: str,
        operation: str,
        result: dict[str, object],
        *,
        session_id: UUID | None = None,
    ) -> None:
        command_ledger_model = ModelRegistry.get("CommandLedger")
        row = command_ledger_model(
            id=uuid4(),
            command_id=command_id,
            operation=operation,
            result_json=result,
            session_id=session_id,
        )
        self._session.add(row)
        self._session.flush()


class SyncSqlAlchemyAgentRepo:
    """Sync SQLAlchemy implementation of AgentRepository."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def register_agent(self, agent: AgentMetadata) -> None:
        agent_record_model = ModelRegistry.get("AgentRecord")
        existing = self._session.get(agent_record_model, agent.id)
        if existing:
            existing.name = agent.name
            existing.version = agent.version
            existing.tags = agent.tags
            existing.capabilities = [c.model_dump(mode="json") for c in agent.capabilities]
        else:
            record = agent_record_model(
                id=agent.id,
                name=agent.name,
                version=agent.version,
                tags=agent.tags,
                capabilities=[c.model_dump(mode="json") for c in agent.capabilities],
            )
            self._session.add(record)
        self._session.flush()

    def get_agent(self, agent_id: str) -> AgentMetadata | None:
        agent_record_model = ModelRegistry.get("AgentRecord")
        row = self._session.get(agent_record_model, agent_id)
        if not row:
            return None
        return AgentMetadata(
            id=row.id,
            name=row.name,
            version=row.version,
            tags=row.tags,
            capabilities=[AgentCapability(**c) for c in row.capabilities],
            created_at=row.created_at,
        )

    def list_agents(self, tags: list[str] | None = None) -> list[AgentMetadata]:
        agent_record_model = ModelRegistry.get("AgentRecord")
        query = select(agent_record_model)
        result = self._session.execute(query)
        rows = result.scalars().all()
        agents = [
            AgentMetadata(
                id=row.id,
                name=row.name,
                version=row.version,
                tags=row.tags,
                capabilities=[AgentCapability(**c) for c in row.capabilities],
                created_at=row.created_at,
            )
            for row in rows
        ]
        if tags:
            return [a for a in agents if any(t in a.tags for t in tags)]
        return agents

    def record_delegation(self, delegation: DelegationProposal) -> None:
        delegation_record_model = ModelRegistry.get("DelegationRecord")
        record = delegation_record_model(
            id=delegation.id,
            source_agent_id=delegation.source_agent_id,
            target_agent_id=delegation.target_agent_id,
            task_description=delegation.task_description,
            risk_score=delegation.risk_score,
            metadata_json=delegation.metadata,
            created_at=delegation.created_at,
        )
        self._session.add(record)
        self._session.flush()


class SyncSqlAlchemyUnitOfWork:
    """Wraps a single sync Session, exposing all repositories."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self.session_repo = SyncSqlAlchemySessionRepo(session)
        self.event_repo = SyncSqlAlchemyEventRepo(session)
        self.approval_repo = SyncSqlAlchemyApprovalRepo(session)
        self.proposal_repo = SyncSqlAlchemyProposalRepo(session)
        self.agent_repo = SyncSqlAlchemyAgentRepo(session)
        self.command_repo = SyncSqlAlchemyCommandRepo(session)

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()
