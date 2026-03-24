"""Evaluator protocol and result types."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from agent_control_plane.types.policies import PolicySnapshot
from agent_control_plane.types.proposals import ActionProposal


class EvaluatorResult(BaseModel):
    """Result of a single evaluator execution."""

    allow: bool
    reason: str
    metadata: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class Evaluator(Protocol):
    """Protocol for pluggable policy evaluators."""

    @property
    def name(self) -> str: ...

    @property
    def config_schema(self) -> type[BaseModel] | None: ...

    async def evaluate(self, proposal: ActionProposal, policy: PolicySnapshot) -> EvaluatorResult: ...
