"""Wire protocol frame definitions for control plane communication."""

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

from .enums import ActionName, EventKind, parse_action_name


class RequestFrame(BaseModel):
    """Inbound request envelope."""

    frame_kind: Literal["request"] = "request"
    request_id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    action: ActionName
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    correlation_id: UUID | None = None

    @field_validator("action", mode="before")
    @classmethod
    def _parse_action(cls, value: ActionName | str) -> ActionName:
        return parse_action_name(value)


class ResponseFrame(BaseModel):
    """Outbound response envelope."""

    frame_kind: Literal["response"] = "response"
    request_id: UUID
    session_id: UUID
    success: bool
    payload: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class EventFrame(BaseModel):
    """Append-only event record."""

    frame_kind: Literal["event"] = "event"
    event_id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    seq: int
    event_kind: EventKind
    agent_id: str | None = None
    correlation_id: UUID | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    state_bearing: bool = False
    routing_decision: dict[str, Any] | None = None
    routing_reason: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
