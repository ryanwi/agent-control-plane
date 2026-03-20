"""Companion gateway starter for agent-control-plane."""

from __future__ import annotations

from dataclasses import dataclass
from secrets import compare_digest
from typing import Any, Protocol
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from agent_control_plane.sync import KillResult
from agent_control_plane.types import (
    ApprovalDecisionType,
    ApprovalStatus,
    ApprovalTicket,
    Page,
    SessionHealth,
    SessionState,
    SessionStatus,
    StateChangePage,
)
from agent_control_plane.types.enums import BudgetPeriod
from agent_control_plane.types.token_governance import (
    IdentityContext,
    TokenBudgetConfig,
    TokenBudgetState,
    TokenUsageSummary,
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
    ) -> Page[ApprovalTicket]: ...

    async def get_ticket(self, ticket_id: UUID) -> ApprovalTicket | None: ...

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
    ) -> ApprovalTicket: ...

    async def deny_ticket(
        self, ticket_id: UUID, *, reason: str = "", command_id: str | None = None
    ) -> ApprovalTicket: ...

    async def get_state_change_feed(
        self,
        *,
        session_id: UUID | None = None,
        cursor: int = 0,
        limit: int = 100,
    ) -> StateChangePage: ...

    async def get_health_snapshot(self) -> SessionHealth: ...

    async def kill_session(
        self, session_id: UUID, *, reason: str = "Kill switch triggered", command_id: str | None = None
    ) -> KillResult: ...

    async def kill_system(self, *, reason: str = "System halt", command_id: str | None = None) -> KillResult: ...


class TokenBudgetFacadeProtocol(Protocol):
    async def list_token_budget_configs(self, identity: IdentityContext) -> list[TokenBudgetConfig]: ...
    async def create_token_budget_config(self, config: TokenBudgetConfig) -> TokenBudgetConfig: ...
    async def get_token_budget_states(self, identity: IdentityContext) -> list[TokenBudgetState]: ...
    async def get_token_usage_summary(
        self, identity: IdentityContext, period: BudgetPeriod, window_start: Any
    ) -> TokenUsageSummary | None: ...


class CreateTokenBudgetConfigRequest(BaseModel):
    user_id: str | None = None
    org_id: str | None = None
    team_id: str | None = None
    period: str
    max_tokens: int | None = None
    max_cost_usd: str | None = None
    allowed_models: list[str] | None = None


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


class ErrorResponse(BaseModel):
    error: str
    details: dict[str, Any] | None = None


class AuthPolicy(Protocol):
    async def authorize(self, authorization: str | None) -> None: ...


@dataclass(frozen=True)
class AllowAllAuthPolicy:
    async def authorize(self, authorization: str | None) -> None:
        _ = authorization


@dataclass(frozen=True)
class DenyAllAuthPolicy:
    message: str = "Authentication is required. Configure an auth policy for this gateway."

    async def authorize(self, authorization: str | None) -> None:
        _ = authorization
        raise HTTPException(status_code=401, detail=self.message)


@dataclass(frozen=True)
class BearerTokenAuthPolicy:
    token: str

    async def authorize(self, authorization: str | None) -> None:
        if authorization is None:
            raise HTTPException(status_code=401, detail="Missing Authorization header")
        scheme, _, presented = authorization.partition(" ")
        if scheme.lower() != "bearer" or not presented:
            raise HTTPException(status_code=401, detail="Authorization must be Bearer token")
        if not compare_digest(presented, self.token):
            raise HTTPException(status_code=401, detail="Invalid bearer token")


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


def _error_response(status_code: int, error: str, details: dict[str, Any] | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content=ErrorResponse(error=error, details=details).model_dump(mode="json")
    )


def _normalize_conflict_error(exc: ValueError) -> tuple[int, str]:
    text = str(exc).strip() or "Conflict"
    lower = text.lower()
    if "not found" in lower:
        return 404, text
    return 409, text


def create_app(  # noqa: C901
    facade: FacadeProtocol,
    *,
    auth_policy: AuthPolicy | None = None,
    token_budget_facade: TokenBudgetFacadeProtocol | None = None,
) -> FastAPI:
    app = FastAPI(
        title="agent-control-plane-gateway",
        version="0.1.0",
        openapi_url="/openapi.json",
        docs_url="/docs",
    )

    policy = auth_policy or DenyAllAuthPolicy()

    @app.middleware("http")
    async def require_auth(request: Request, call_next: Any) -> Any:
        if request.url.path.startswith("/v1/") or request.url.path == "/dashboard":
            await policy.authorize(request.headers.get("authorization"))
        return await call_next(request)

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict):
            message = str(detail.get("error") or "Request failed")
            details = detail
        else:
            message = str(detail)
            details = None
        return _error_response(exc.status_code, message, details)

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _error_response(422, "Validation failed", {"errors": exc.errors()})

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
        try:
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
        except ValueError as exc:
            status_code, message = _normalize_conflict_error(exc)
            raise HTTPException(status_code=status_code, detail=message) from exc
        return _dump(row)

    @app.post("/v1/tickets/{ticket_id}/deny")
    async def deny_ticket(
        ticket_id: UUID,
        body: DenyTicketRequest | None = None,
        x_idempotency_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        req = body or DenyTicketRequest()
        try:
            row = await facade.deny_ticket(ticket_id, reason=req.reason, command_id=x_idempotency_key)
        except ValueError as exc:
            status_code, message = _normalize_conflict_error(exc)
            raise HTTPException(status_code=status_code, detail=message) from exc
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

    # Token governance endpoints (optional — only registered if facade provided)
    if token_budget_facade is not None:
        _tb = token_budget_facade

        def _parse_identity(
            user_id: str | None = None, org_id: str | None = None, team_id: str | None = None
        ) -> IdentityContext:
            from agent_control_plane.types.ids import OrgId, TeamId, UserId

            return IdentityContext(
                user_id=UserId(user_id) if user_id else None,
                org_id=OrgId(org_id) if org_id else None,
                team_id=TeamId(team_id) if team_id else None,
            )

        @app.get("/v1/token-budgets")
        async def list_token_budgets(
            user_id: str | None = None, org_id: str | None = None, team_id: str | None = None
        ) -> list[dict[str, Any]]:
            identity = _parse_identity(user_id, org_id, team_id)
            configs = await _tb.list_token_budget_configs(identity)
            return [_dump(c) for c in configs]

        @app.post("/v1/token-budgets", status_code=201)
        async def create_token_budget(body: CreateTokenBudgetConfigRequest) -> dict[str, Any]:
            from decimal import Decimal

            from agent_control_plane.types.ids import ModelId, OrgId, TeamId, UserId

            identity = IdentityContext(
                user_id=UserId(body.user_id) if body.user_id else None,
                org_id=OrgId(body.org_id) if body.org_id else None,
                team_id=TeamId(body.team_id) if body.team_id else None,
            )
            config = TokenBudgetConfig(
                identity=identity,
                period=BudgetPeriod(body.period),
                max_tokens=body.max_tokens,
                max_cost_usd=Decimal(body.max_cost_usd) if body.max_cost_usd else None,
                allowed_models=[ModelId(m) for m in body.allowed_models] if body.allowed_models else None,
            )
            created = await _tb.create_token_budget_config(config)
            return _dump(created)

        @app.get("/v1/token-budgets/states")
        async def get_token_budget_states(
            user_id: str | None = None, org_id: str | None = None, team_id: str | None = None
        ) -> list[dict[str, Any]]:
            identity = _parse_identity(user_id, org_id, team_id)
            states = await _tb.get_token_budget_states(identity)
            return [_dump(s) for s in states]

        @app.get("/v1/token-budgets/usage-summary")
        async def get_token_usage_summary(
            period: str,
            window_start: str,
            user_id: str | None = None,
            org_id: str | None = None,
            team_id: str | None = None,
        ) -> dict[str, Any]:
            from datetime import datetime

            identity = _parse_identity(user_id, org_id, team_id)
            parsed_start = datetime.fromisoformat(window_start)
            summary = await _tb.get_token_usage_summary(identity, BudgetPeriod(period), parsed_start)
            if summary is None:
                raise HTTPException(status_code=404, detail="No usage data found")
            return _dump(summary)

    return app
