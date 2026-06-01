"""Evaluator protocol and result types."""

from __future__ import annotations

from collections.abc import Mapping
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


@runtime_checkable
class ResponseEvaluator(Protocol):
    """Protocol for evaluators that screen tool *output* before it re-enters context.

    Unlike :class:`Evaluator`, which judges the proposal (the request) before execution,
    a response evaluator inspects the content a tool returned. It is intentionally
    synchronous and decoupled from the MCP layer: it receives a generic output mapping,
    never the gateway's ``ToolCallResult``, so ``evaluators/`` keeps no dependency on
    ``mcp/``. Deny means the output is withheld and the call fails closed.
    """

    @property
    def name(self) -> str: ...

    def evaluate_response(
        self, proposal: ActionProposal, output: Mapping[str, Any], policy: PolicySnapshot
    ) -> EvaluatorResult: ...
