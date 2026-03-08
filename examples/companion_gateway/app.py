"""Companion gateway starter for agent-control-plane."""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from agent_control_plane.sync import KillResultDTO
from agent_control_plane.types import (
    ApprovalDecisionType,
    ApprovalStatus,
    ApprovalTicketDTO,
    PageDTO,
    SessionHealthDTO,
    SessionState,
    SessionStatus,
    StateChangePageDTO,
)


class FacadeProtocol(Protocol):
    async def list_sessions(
        self, *, statuses: list[SessionStatus] | None = None, limit: int = 50
    ) -> list[SessionState]: ...

    async def get_session(self, session_id: UUID) -> SessionState | None: ...

    async def list_tickets(
        self,
        *,
        session_id: UUID | None = None,
        statuses: list[ApprovalStatus] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> PageDTO[ApprovalTicketDTO]: ...

    async def get_ticket(self, ticket_id: UUID) -> ApprovalTicketDTO | None: ...

    async def approve_ticket(
        self,
        ticket_id: UUID,
        *,
        decided_by: str = "operator",
        reason: str | None = None,
        decision_type: ApprovalDecisionType = ApprovalDecisionType.ALLOW_ONCE,
        scope_resource_ids: list[str] | None = None,
        scope_max_cost: Any | None = None,
        scope_max_action_count: int | None = None,
        scope_expiry: Any | None = None,
        command_id: str | None = None,
    ) -> ApprovalTicketDTO: ...

    async def deny_ticket(
        self, ticket_id: UUID, *, reason: str = "", command_id: str | None = None
    ) -> ApprovalTicketDTO: ...

    async def get_state_change_feed(
        self,
        *,
        session_id: UUID | None = None,
        cursor: int = 0,
        limit: int = 100,
    ) -> StateChangePageDTO: ...

    async def get_health_snapshot(self) -> SessionHealthDTO: ...

    async def kill_session(
        self, session_id: UUID, *, reason: str = "Kill switch triggered", command_id: str | None = None
    ) -> KillResultDTO: ...

    async def kill_system(self, *, reason: str = "System halt", command_id: str | None = None) -> KillResultDTO: ...


class ApproveTicketRequest(BaseModel):
    decided_by: str = "operator"
    reason: str | None = None
    decision_type: ApprovalDecisionType = ApprovalDecisionType.ALLOW_ONCE
    scope_resource_ids: list[str] | None = None
    scope_max_cost: str | None = None
    scope_max_action_count: int | None = None
    scope_expiry: str | None = None


class DenyTicketRequest(BaseModel):
    reason: str = ""


class KillRequest(BaseModel):
    reason: str | None = None


def _dump(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")


def _parse_statuses(raw: str | None, enum_type: type[SessionStatus] | type[ApprovalStatus]) -> list[Any] | None:
    if not raw:
        return None
    values = [part.strip() for part in raw.split(",") if part.strip()]
    try:
        return [enum_type(v) for v in values]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid status value: {exc}") from exc


def create_app(facade: FacadeProtocol) -> FastAPI:  # noqa: C901
    app = FastAPI(
        title="agent-control-plane-gateway",
        version="0.1.0",
        openapi_url="/openapi.json",
        docs_url="/docs",
    )

    @app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
    async def dashboard() -> str:
        return """
<!doctype html>
<html>
  <head><title>Control Plane Dashboard</title></head>
  <body style="font-family: ui-monospace, Menlo, monospace; margin: 24px;">
    <h1>Control Plane Dashboard (Starter)</h1>
    <p>Read-only links:</p>
    <ul>
      <li><a href="/v1/health">/v1/health</a></li>
      <li><a href="/v1/sessions?limit=20">/v1/sessions</a></li>
      <li><a href="/v1/tickets?limit=20">/v1/tickets</a></li>
      <li><a href="/v1/events/state-changes?limit=20">/v1/events/state-changes</a></li>
      <li><a href="/docs">Interactive API docs</a></li>
    </ul>
  </body>
</html>
"""

    @app.get("/v1/sessions")
    async def list_sessions(
        statuses: str | None = Query(default=None), limit: int = Query(default=50, ge=1, le=1000)
    ) -> list[dict[str, Any]]:
        parsed = _parse_statuses(statuses, SessionStatus)
        rows = await facade.list_sessions(statuses=parsed, limit=limit)
        return [_dump(row) for row in rows]

    @app.get("/v1/sessions/{session_id}")
    async def get_session(session_id: UUID) -> dict[str, Any]:
        row = await facade.get_session(session_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return _dump(row)

    @app.get("/v1/tickets")
    async def list_tickets(
        session_id: UUID | None = None,
        statuses: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        parsed = _parse_statuses(statuses, ApprovalStatus)
        page = await facade.list_tickets(session_id=session_id, statuses=parsed, limit=limit, offset=offset)
        return _dump(page)

    @app.get("/v1/tickets/{ticket_id}")
    async def get_ticket(ticket_id: UUID) -> dict[str, Any]:
        row = await facade.get_ticket(ticket_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Ticket not found")
        return _dump(row)

    @app.post("/v1/tickets/{ticket_id}/approve")
    async def approve_ticket(
        ticket_id: UUID,
        body: ApproveTicketRequest | None = None,
        x_idempotency_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        req = body or ApproveTicketRequest()
        row = await facade.approve_ticket(
            ticket_id,
            decided_by=req.decided_by,
            reason=req.reason,
            decision_type=req.decision_type,
            scope_resource_ids=req.scope_resource_ids,
            scope_max_cost=req.scope_max_cost,
            scope_max_action_count=req.scope_max_action_count,
            scope_expiry=req.scope_expiry,
            command_id=x_idempotency_key,
        )
        return _dump(row)

    @app.post("/v1/tickets/{ticket_id}/deny")
    async def deny_ticket(
        ticket_id: UUID,
        body: DenyTicketRequest | None = None,
        x_idempotency_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        req = body or DenyTicketRequest()
        row = await facade.deny_ticket(ticket_id, reason=req.reason, command_id=x_idempotency_key)
        return _dump(row)

    @app.get("/v1/events/state-changes")
    async def get_state_change_feed(
        session_id: UUID | None = None,
        cursor: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> dict[str, Any]:
        page = await facade.get_state_change_feed(session_id=session_id, cursor=cursor, limit=limit)
        return _dump(page)

    @app.get("/v1/health")
    async def get_health() -> dict[str, Any]:
        snapshot = await facade.get_health_snapshot()
        return _dump(snapshot)

    @app.post("/v1/kill/session/{session_id}")
    async def kill_session(
        session_id: UUID,
        body: KillRequest | None = None,
        x_idempotency_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        reason = (body.reason if body else None) or "Kill switch triggered"
        result = await facade.kill_session(session_id, reason=reason, command_id=x_idempotency_key)
        return _dump(result)

    @app.post("/v1/kill/system")
    async def kill_system(
        body: KillRequest | None = None, x_idempotency_key: str | None = Header(default=None)
    ) -> dict[str, Any]:
        reason = (body.reason if body else None) or "System halt"
        result = await facade.kill_system(reason=reason, command_id=x_idempotency_key)
        return _dump(result)

    return app
