"""Wire protocol frame definitions for control plane communication."""

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class RequestFrame(BaseModel):
    """Inbound request envelope."""

    frame_kind: Literal["request"] = "request"
    request_id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    action: str
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    correlation_id: UUID | None = None


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
    event_kind: str
    agent_id: str | None = None
    correlation_id: UUID | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    routing_decision: dict[str, Any] | None = None
    routing_reason: str | None = None
