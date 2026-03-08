"""Runnable gateway entrypoint for local integration testing."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from agent_control_plane.sync import KillResultDTO
from agent_control_plane.types import (
    ApprovalDecisionType,
    ApprovalStatus,
    ApprovalTicketDTO,
    EventKind,
    ExecutionMode,
    KillSwitchScope,
    PageDTO,
    SessionHealthDTO,
    SessionState,
    SessionStatus,
    StateChangeDTO,
    StateChangePageDTO,
)
from agent_control_plane.types.frames import EventFrame

from .app import AllowAllAuthPolicy, BearerTokenAuthPolicy, FacadeProtocol, create_app


class DemoFacade(FacadeProtocol):
    """In-memory facade implementation for bootstrapping the gateway starter."""

    async def list_sessions(
        self, *, statuses: list[SessionStatus] | None = None, limit: int = 50
    ) -> list[SessionState]:
        _ = (statuses, limit)
        now = datetime.now(UTC)
        return [
            SessionState(
                id=uuid4(),
                session_name="demo-session",
                status=SessionStatus.ACTIVE,
                execution_mode=ExecutionMode.DRY_RUN,
                max_cost=Decimal("1000"),
                used_cost=Decimal("10"),
                max_action_count=100,
                used_action_count=1,
                created_at=now,
                updated_at=now,
            )
        ]

    async def get_session(self, session_id: UUID) -> SessionState | None:
        _ = session_id
        return None

    async def list_tickets(
        self,
        *,
        session_id: UUID | None = None,
        statuses: list[ApprovalStatus] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> PageDTO[ApprovalTicketDTO]:
        _ = (session_id, statuses, limit, offset)
        return PageDTO(items=[], next_offset=None)

    async def get_ticket(self, ticket_id: UUID) -> ApprovalTicketDTO | None:
        _ = ticket_id
        return None

    async def approve_ticket(
        self,
        ticket_id: UUID,
        *,
        decided_by: str = "operator",
        reason: str | None = None,
        decision_type: ApprovalDecisionType = ApprovalDecisionType.ALLOW_ONCE,
        scope_resource_ids: list[str] | None = None,
        scope_max_cost: str | None = None,
        scope_max_action_count: int | None = None,
        scope_expiry: str | None = None,
        command_id: str | None = None,
    ) -> ApprovalTicketDTO:
        _ = (
            ticket_id,
            decided_by,
            reason,
            decision_type,
            scope_resource_ids,
            scope_max_cost,
            scope_max_action_count,
            scope_expiry,
            command_id,
        )
        raise ValueError("Ticket not found")

    async def deny_ticket(
        self, ticket_id: UUID, *, reason: str = "", command_id: str | None = None
    ) -> ApprovalTicketDTO:
        _ = (ticket_id, reason, command_id)
        raise ValueError("Ticket not found")

    async def get_state_change_feed(
        self,
        *,
        session_id: UUID | None = None,
        cursor: int = 0,
        limit: int = 100,
    ) -> StateChangePageDTO:
        _ = (session_id, cursor, limit)
        event = EventFrame(
            session_id=uuid4(),
            seq=1,
            event_kind=EventKind.CYCLE_STARTED,
            payload={"source": "demo"},
            state_bearing=True,
        )
        return StateChangePageDTO(items=[StateChangeDTO(cursor=1, event=event)], next_cursor=None)

    async def get_health_snapshot(self) -> SessionHealthDTO:
        return SessionHealthDTO(
            total_sessions=1,
            active_sessions=1,
            created_sessions=0,
            paused_sessions=0,
            sessions_with_active_cycles=0,
            pending_tickets=0,
        )

    async def kill_session(
        self, session_id: UUID, *, reason: str = "Kill switch triggered", command_id: str | None = None
    ) -> KillResultDTO:
        _ = (reason, command_id)
        return KillResultDTO(scope=KillSwitchScope.SESSION_ABORT, session_id=session_id, tickets_denied=0)

    async def kill_system(self, *, reason: str = "System halt", command_id: str | None = None) -> KillResultDTO:
        _ = (reason, command_id)
        return KillResultDTO(scope=KillSwitchScope.SYSTEM_HALT, sessions_aborted=0, tickets_denied=0)


def _auth_policy() -> AllowAllAuthPolicy | BearerTokenAuthPolicy:
    token = os.getenv("ACP_GATEWAY_BEARER_TOKEN")
    if token:
        return BearerTokenAuthPolicy(token=token)
    return AllowAllAuthPolicy()


app = create_app(DemoFacade(), auth_policy=_auth_policy())
