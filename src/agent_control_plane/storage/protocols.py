"""Repository protocols for storage abstraction.

Defines sync and async protocol pairs that decouple engines from any
specific database backend (SQLAlchemy, in-memory, etc.).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from agent_control_plane.types.agents import AgentMetadata, DelegationProposal
from agent_control_plane.types.approvals import ApprovalTicketDTO
from agent_control_plane.types.enums import ApprovalStatus, EventKind, ProposalStatus, SessionStatus
from agent_control_plane.types.frames import EventFrame
from agent_control_plane.types.ids import AgentId, IdempotencyKey, ResourceId
from agent_control_plane.types.proposals import ActionProposalDTO
from agent_control_plane.types.query import CommandResultDTO
from agent_control_plane.types.sessions import BudgetInfo, SessionState

# ---------------------------------------------------------------------------
# Session repositories
# ---------------------------------------------------------------------------


@runtime_checkable
class SessionRepository(Protocol):
    def get_session(self, session_id: UUID) -> SessionState | None: ...
    def get_session_for_update(self, session_id: UUID) -> SessionState: ...
    def create_session(self, **kwargs: Any) -> SessionState: ...
    def update_session(self, session_id: UUID, **fields: Any) -> None: ...
    def set_active_cycle(self, session_id: UUID, cycle_id: UUID | None) -> None: ...
    def list_sessions(self, statuses: list[SessionStatus] | None = None, limit: int = 50) -> list[SessionState]: ...
    def increment_budget(self, session_id: UUID, cost: Decimal, action_count: int) -> None: ...
    def get_budget(self, session_id: UUID) -> BudgetInfo: ...
    def create_policy(self, **kwargs: Any) -> UUID: ...
    def create_seq_counter(self, session_id: UUID) -> None: ...


@runtime_checkable
class AsyncSessionRepository(Protocol):
    async def get_session(self, session_id: UUID) -> SessionState | None: ...
    async def get_session_for_update(self, session_id: UUID) -> SessionState: ...
    async def create_session(self, **kwargs: Any) -> SessionState: ...
    async def update_session(self, session_id: UUID, **fields: Any) -> None: ...
    async def set_active_cycle(self, session_id: UUID, cycle_id: UUID | None) -> None: ...
    async def list_sessions(
        self, statuses: list[SessionStatus] | None = None, limit: int = 50
    ) -> list[SessionState]: ...
    async def increment_budget(self, session_id: UUID, cost: Decimal, action_count: int) -> None: ...
    async def get_budget(self, session_id: UUID) -> BudgetInfo: ...
    async def create_policy(self, **kwargs: Any) -> UUID: ...
    async def create_seq_counter(self, session_id: UUID) -> None: ...


# ---------------------------------------------------------------------------
# Event repositories
# ---------------------------------------------------------------------------


@runtime_checkable
class EventRepository(Protocol):
    def append(
        self,
        session_id: UUID,
        event_kind: EventKind,
        payload: dict[str, Any],
        *,
        state_bearing: bool = False,
        agent_id: AgentId | None = None,
        correlation_id: UUID | None = None,
        routing_decision: dict[str, Any] | None = None,
        routing_reason: str | None = None,
        idempotency_key: IdempotencyKey | None = None,
    ) -> int: ...

    def replay(self, session_id: UUID, after_seq: int = 0, limit: int = 100) -> list[EventFrame]: ...
    def get_last_event(self, session_id: UUID) -> EventFrame | None: ...
    def list_state_bearing_events(
        self, *, session_id: UUID | None = None, limit: int = 100, offset: int = 0
    ) -> list[EventFrame]: ...


@runtime_checkable
class AsyncEventRepository(Protocol):
    async def append(
        self,
        session_id: UUID,
        event_kind: EventKind,
        payload: dict[str, Any],
        *,
        state_bearing: bool = False,
        agent_id: AgentId | None = None,
        correlation_id: UUID | None = None,
        routing_decision: dict[str, Any] | None = None,
        routing_reason: str | None = None,
        idempotency_key: IdempotencyKey | None = None,
    ) -> int: ...

    async def replay(self, session_id: UUID, after_seq: int = 0, limit: int = 100) -> list[EventFrame]: ...
    async def get_last_event(self, session_id: UUID) -> EventFrame | None: ...
    async def list_state_bearing_events(
        self, *, session_id: UUID | None = None, limit: int = 100, offset: int = 0
    ) -> list[EventFrame]: ...


# ---------------------------------------------------------------------------
# Approval repositories
# ---------------------------------------------------------------------------


@runtime_checkable
class ApprovalRepository(Protocol):
    def create_ticket(self, session_id: UUID, proposal_id: UUID, timeout_at: datetime) -> ApprovalTicketDTO: ...

    def get_ticket(self, ticket_id: UUID) -> ApprovalTicketDTO | None: ...
    def get_pending_ticket_for_update(self, ticket_id: UUID) -> ApprovalTicketDTO: ...
    def update_ticket(self, ticket_id: UUID, **fields: Any) -> None: ...
    def list_tickets(
        self,
        *,
        session_id: UUID | None = None,
        statuses: list[ApprovalStatus] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ApprovalTicketDTO]: ...
    def get_pending_tickets(self, session_id: UUID | None = None) -> list[ApprovalTicketDTO]: ...
    def get_session_scope_tickets(self, session_id: UUID) -> list[ApprovalTicketDTO]: ...
    def decrement_scope_count(self, ticket_id: UUID) -> None: ...
    def deny_all_pending(self, session_id: UUID) -> int: ...
    def expire_timed_out(self) -> list[ApprovalTicketDTO]: ...


@runtime_checkable
class AsyncApprovalRepository(Protocol):
    async def create_ticket(self, session_id: UUID, proposal_id: UUID, timeout_at: datetime) -> ApprovalTicketDTO: ...

    async def get_ticket(self, ticket_id: UUID) -> ApprovalTicketDTO | None: ...
    async def get_pending_ticket_for_update(self, ticket_id: UUID) -> ApprovalTicketDTO: ...
    async def update_ticket(self, ticket_id: UUID, **fields: Any) -> None: ...
    async def list_tickets(
        self,
        *,
        session_id: UUID | None = None,
        statuses: list[ApprovalStatus] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ApprovalTicketDTO]: ...
    async def get_pending_tickets(self, session_id: UUID | None = None) -> list[ApprovalTicketDTO]: ...
    async def get_session_scope_tickets(self, session_id: UUID) -> list[ApprovalTicketDTO]: ...
    async def decrement_scope_count(self, ticket_id: UUID) -> None: ...
    async def deny_all_pending(self, session_id: UUID) -> int: ...
    async def expire_timed_out(self) -> list[ApprovalTicketDTO]: ...


# ---------------------------------------------------------------------------
# Proposal repositories
# ---------------------------------------------------------------------------


@runtime_checkable
class ProposalRepository(Protocol):
    def create_proposal(self, proposal: ActionProposalDTO) -> ActionProposalDTO: ...
    def get_proposal(self, proposal_id: UUID) -> ActionProposalDTO | None: ...
    def list_proposals(
        self,
        *,
        session_id: UUID | None = None,
        statuses: list[ProposalStatus] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ActionProposalDTO]: ...
    def update_status(self, proposal_id: UUID, status: ProposalStatus) -> None: ...
    def has_pending_for_resource(self, session_id: UUID, resource_id: ResourceId) -> bool: ...


@runtime_checkable
class AsyncProposalRepository(Protocol):
    async def create_proposal(self, proposal: ActionProposalDTO) -> ActionProposalDTO: ...
    async def get_proposal(self, proposal_id: UUID) -> ActionProposalDTO | None: ...
    async def list_proposals(
        self,
        *,
        session_id: UUID | None = None,
        statuses: list[ProposalStatus] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ActionProposalDTO]: ...
    async def update_status(self, proposal_id: UUID, status: ProposalStatus) -> None: ...
    async def has_pending_for_resource(self, session_id: UUID, resource_id: ResourceId) -> bool: ...


# ---------------------------------------------------------------------------
# Command idempotency repositories
# ---------------------------------------------------------------------------


@runtime_checkable
class CommandRepository(Protocol):
    def get_command(self, command_id: str) -> CommandResultDTO | None: ...
    def record_command(
        self,
        command_id: str,
        operation: str,
        result: dict[str, object],
        *,
        session_id: UUID | None = None,
    ) -> None: ...


@runtime_checkable
class AsyncCommandRepository(Protocol):
    async def get_command(self, command_id: str) -> CommandResultDTO | None: ...
    async def record_command(
        self,
        command_id: str,
        operation: str,
        result: dict[str, object],
        *,
        session_id: UUID | None = None,
    ) -> None: ...


...
# ---------------------------------------------------------------------------
# Agent repositories
# ---------------------------------------------------------------------------


@runtime_checkable
class AgentRepository(Protocol):
    def register_agent(self, agent: AgentMetadata) -> None: ...
    def get_agent(self, agent_id: AgentId) -> AgentMetadata | None: ...
    def list_agents(self, tags: list[str] | None = None) -> list[AgentMetadata]: ...
    def record_delegation(self, delegation: DelegationProposal) -> None: ...


@runtime_checkable
class AsyncAgentRepository(Protocol):
    async def register_agent(self, agent: AgentMetadata) -> None: ...
    async def get_agent(self, agent_id: AgentId) -> AgentMetadata | None: ...
    async def list_agents(self, tags: list[str] | None = None) -> list[AgentMetadata]: ...
    async def record_delegation(self, delegation: DelegationProposal) -> None: ...


# ---------------------------------------------------------------------------
# Unit of Work
# ---------------------------------------------------------------------------


@runtime_checkable
class SyncUnitOfWork(Protocol):
    session_repo: SessionRepository
    event_repo: EventRepository
    approval_repo: ApprovalRepository
    proposal_repo: ProposalRepository
    agent_repo: AgentRepository
    command_repo: CommandRepository

    def commit(self) -> None: ...
    def rollback(self) -> None: ...


@runtime_checkable
class AsyncUnitOfWork(Protocol):
    session_repo: AsyncSessionRepository
    event_repo: AsyncEventRepository
    approval_repo: AsyncApprovalRepository
    proposal_repo: AsyncProposalRepository
    agent_repo: AsyncAgentRepository
    command_repo: AsyncCommandRepository

    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
