from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, RefResolver

from agent_control_plane.sync import KillResultDTO
from agent_control_plane.types.approvals import ApprovalTicketDTO
from agent_control_plane.types.enums import (
    ApprovalDecisionType,
    ApprovalStatus,
    EventKind,
    ExecutionMode,
    KillSwitchScope,
    SessionStatus,
)
from agent_control_plane.types.frames import EventFrame
from agent_control_plane.types.query import PageDTO, SessionHealthDTO, StateChangeDTO, StateChangePageDTO
from agent_control_plane.types.sessions import SessionState
from examples.companion_gateway.app import AllowAllAuthPolicy, create_app

pytestmark = pytest.mark.filterwarnings("ignore:jsonschema.RefResolver is deprecated:DeprecationWarning")


class _StubFacade:
    def __init__(self, *, approve_conflict: bool = False) -> None:
        self.session_id = uuid4()
        self.ticket_id = uuid4()
        self.proposal_id = uuid4()
        self.approve_conflict = approve_conflict

    async def list_sessions(
        self, *, statuses: list[SessionStatus] | None = None, limit: int = 50
    ) -> list[SessionState]:
        _ = (statuses, limit)
        return [self._session()]

    async def get_session(self, session_id: UUID) -> SessionState | None:
        return self._session() if session_id == self.session_id else None

    async def list_tickets(
        self,
        *,
        session_id: UUID | None = None,
        statuses: list[ApprovalStatus] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> PageDTO[ApprovalTicketDTO]:
        _ = (session_id, statuses, limit, offset)
        return PageDTO(items=[self._ticket()], next_offset=None)

    async def get_ticket(self, ticket_id: UUID) -> ApprovalTicketDTO | None:
        return self._ticket() if ticket_id == self.ticket_id else None

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
        if self.approve_conflict:
            raise ValueError("Ticket already decided")
        ticket = self._ticket()
        return ticket.model_copy(
            update={
                "status": ApprovalStatus.APPROVED,
                "decision_type": decision_type,
                "decided_by": decided_by,
                "decision_reason": reason,
            }
        )

    async def deny_ticket(
        self, ticket_id: UUID, *, reason: str = "", command_id: str | None = None
    ) -> ApprovalTicketDTO:
        _ = (ticket_id, command_id)
        ticket = self._ticket()
        return ticket.model_copy(update={"status": ApprovalStatus.DENIED, "decision_reason": reason})

    async def get_state_change_feed(
        self,
        *,
        session_id: UUID | None = None,
        cursor: int = 0,
        limit: int = 100,
    ) -> StateChangePageDTO:
        _ = (session_id, cursor, limit)
        event = EventFrame(
            session_id=self.session_id,
            seq=1,
            event_kind=EventKind.CYCLE_STARTED,
            payload={"cycle_id": str(uuid4())},
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
            pending_tickets=1,
        )

    async def kill_session(
        self, session_id: UUID, *, reason: str = "Kill switch triggered", command_id: str | None = None
    ) -> KillResultDTO:
        _ = (reason, command_id)
        return KillResultDTO(scope=KillSwitchScope.SESSION_ABORT, session_id=session_id, tickets_denied=1)

    async def kill_system(self, *, reason: str = "System halt", command_id: str | None = None) -> KillResultDTO:
        _ = (reason, command_id)
        return KillResultDTO(scope=KillSwitchScope.SYSTEM_HALT, sessions_aborted=3, tickets_denied=2)

    def _session(self) -> SessionState:
        now = datetime.now(UTC)
        return SessionState(
            id=self.session_id,
            session_name="gateway-contract",
            status=SessionStatus.ACTIVE,
            execution_mode=ExecutionMode.DRY_RUN,
            max_cost=Decimal("100"),
            used_cost=Decimal("10"),
            max_action_count=50,
            used_action_count=2,
            created_at=now,
            updated_at=now,
        )

    def _ticket(self) -> ApprovalTicketDTO:
        now = datetime.now(UTC)
        return ApprovalTicketDTO(
            id=self.ticket_id,
            session_id=self.session_id,
            proposal_id=self.proposal_id,
            status=ApprovalStatus.PENDING,
            created_at=now,
            timeout_at=now,
        )


def _openapi() -> dict[str, Any]:
    path = Path("docs/openapi/control-plane-v1.yml")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _validate_schema(spec: dict[str, Any], schema: dict[str, Any], payload: Any) -> None:
    validator = Draft202012Validator(schema, resolver=RefResolver.from_schema(spec))
    validator.validate(payload)


def _resolve_ref(spec: dict[str, Any], obj: dict[str, Any]) -> dict[str, Any]:
    ref = obj.get("$ref")
    if ref is None:
        return obj
    parts = ref.lstrip("#/").split("/")
    resolved: Any = spec
    for part in parts:
        resolved = resolved[part]
    if not isinstance(resolved, dict):
        raise TypeError(f"Expected mapping at ref {ref}")
    return resolved


def _response_schema(spec: dict[str, Any], path: str, method: str, status_code: str = "200") -> dict[str, Any]:
    response = _resolve_ref(spec, spec["paths"][path][method]["responses"][status_code])
    return response["content"]["application/json"]["schema"]


def _request_schema(spec: dict[str, Any], path: str, method: str) -> dict[str, Any]:
    return spec["paths"][path][method]["requestBody"]["content"]["application/json"]["schema"]


def _client(facade: _StubFacade) -> TestClient:
    return TestClient(create_app(facade, auth_policy=AllowAllAuthPolicy()))


def test_list_sessions_contract() -> None:
    spec = _openapi()
    client = _client(_StubFacade())

    res = client.get("/v1/sessions")
    assert res.status_code == 200
    _validate_schema(spec, _response_schema(spec, "/v1/sessions", "get"), res.json())


def test_get_ticket_contract() -> None:
    spec = _openapi()
    facade = _StubFacade()
    client = _client(facade)

    res = client.get(f"/v1/tickets/{facade.ticket_id}")
    assert res.status_code == 200
    _validate_schema(spec, _response_schema(spec, "/v1/tickets/{ticket_id}", "get"), res.json())


def test_state_change_feed_contract() -> None:
    spec = _openapi()
    client = _client(_StubFacade())

    res = client.get("/v1/events/state-changes?cursor=0&limit=20")
    assert res.status_code == 200
    _validate_schema(spec, _response_schema(spec, "/v1/events/state-changes", "get"), res.json())


def test_health_contract() -> None:
    spec = _openapi()
    client = _client(_StubFacade())

    res = client.get("/v1/health")
    assert res.status_code == 200
    _validate_schema(spec, _response_schema(spec, "/v1/health", "get"), res.json())


def test_kill_system_contract() -> None:
    spec = _openapi()
    client = _client(_StubFacade())

    res = client.post("/v1/kill/system", json={"reason": "ops"})
    assert res.status_code == 200
    _validate_schema(spec, _response_schema(spec, "/v1/kill/system", "post"), res.json())


def test_request_body_contracts() -> None:
    spec = _openapi()
    _validate_schema(
        spec,
        _request_schema(spec, "/v1/tickets/{ticket_id}/approve", "post"),
        {"decided_by": "ops", "decision_type": "allow_once", "reason": "ok"},
    )
    _validate_schema(
        spec,
        _request_schema(spec, "/v1/tickets/{ticket_id}/deny", "post"),
        {"reason": "policy"},
    )
    _validate_schema(
        spec,
        _request_schema(spec, "/v1/kill/system", "post"),
        {"reason": "operator_request"},
    )


def test_not_found_error_contract() -> None:
    spec = _openapi()
    facade = _StubFacade()
    client = _client(facade)

    res = client.get(f"/v1/tickets/{uuid4()}")
    assert res.status_code == 404
    _validate_schema(spec, _response_schema(spec, "/v1/tickets/{ticket_id}", "get", "404"), res.json())


def test_conflict_error_contract() -> None:
    spec = _openapi()
    facade = _StubFacade(approve_conflict=True)
    client = _client(facade)

    res = client.post(f"/v1/tickets/{facade.ticket_id}/approve", json={"reason": "retry"})
    assert res.status_code == 409
    _validate_schema(
        spec,
        _response_schema(spec, "/v1/tickets/{ticket_id}/approve", "post", "409"),
        res.json(),
    )


def test_validation_error_contract() -> None:
    spec = _openapi()
    client = _client(_StubFacade())

    res = client.get("/v1/sessions?statuses=INVALID_STATUS")
    assert res.status_code == 422
    _validate_schema(spec, _response_schema(spec, "/v1/sessions", "get", "422"), res.json())
