"""Query/result DTOs for facade read APIs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, Field

from .frames import EventFrame

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Offset-based page of typed results."""

    items: list[T]
    next_offset: int | None = None


class StateChange(BaseModel):
    """Canonical state change item for projection consumers."""

    cursor: int
    event: EventFrame
    projected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class StateChangePage(BaseModel):
    """Page of state-bearing events with next cursor."""

    items: list[StateChange]
    next_cursor: int | None = None


class SessionHealth(BaseModel):
    """Operational health snapshot for control-plane state."""

    total_sessions: int
    active_sessions: int
    created_sessions: int
    paused_sessions: int
    sessions_with_active_cycles: int
    pending_tickets: int
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CommandResult(BaseModel):
    """Idempotent command ledger item."""

    command_id: str
    operation: str
    result: dict[str, object]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    session_id: UUID | None = None
