"""Wire protocol frame definitions for control plane communication."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

from .enums import ActionValue, EventKind, parse_action_name
from .ids import AgentId, IdempotencyKey


@dataclass(frozen=True)
class EventMetadata:
    """Optional attribution metadata carried through the storage/engine layer."""

    agent_id: AgentId | None = None
    correlation_id: UUID | None = None
    routing_decision: dict[str, Any] | None = None
    routing_reason: str | None = None
    idempotency_key: IdempotencyKey | None = None


@dataclass(frozen=True)
class EmitMetadata:
    """Optional metadata for a facade emit() call — extends EventMetadata with command_id."""

    agent_id: AgentId | None = None
    correlation_id: UUID | None = None
    routing_decision: dict[str, Any] | None = None
    routing_reason: str | None = None
    idempotency_key: IdempotencyKey | None = None
    command_id: IdempotencyKey | None = None

    def as_event_metadata(self) -> EventMetadata:
        """Strip command_id and return the storage-layer metadata view."""
        return EventMetadata(
            agent_id=self.agent_id,
            correlation_id=self.correlation_id,
            routing_decision=self.routing_decision,
            routing_reason=self.routing_reason,
            idempotency_key=self.idempotency_key,
        )


class RequestFrame(BaseModel):
    """Inbound request envelope."""

    frame_kind: Literal["request"] = "request"
    request_id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    action: ActionValue
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    correlation_id: UUID | None = None

    @field_validator("action", mode="before")
    @classmethod
    def _parse_action(cls, value: ActionValue) -> ActionValue:
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
    kind: EventKind
    agent_id: AgentId | None = None
    correlation_id: UUID | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    state_bearing: bool = False
    routing_decision: dict[str, Any] | None = None
    routing_reason: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
