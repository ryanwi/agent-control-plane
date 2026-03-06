"""DTOs for Agent Identity and Delegation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from .enums import ActionName
from .ids import AgentId


class AgentCapability(BaseModel):
    """A specific action an agent is qualified to perform."""

    action: ActionName
    constraints: dict[str, Any] = Field(default_factory=dict)


class AgentMetadata(BaseModel):
    """Identity and capability metadata for a registered agent."""

    id: AgentId
    name: str
    version: str = "1.0.0"
    tags: list[str] = Field(default_factory=list)
    capabilities: list[AgentCapability] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DelegationProposal(BaseModel):
    """A request from one agent to delegate a task to another."""

    id: UUID = Field(default_factory=uuid4)
    source_agent_id: AgentId
    target_agent_id: AgentId
    task_description: str
    risk_score: float = 0.5
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
