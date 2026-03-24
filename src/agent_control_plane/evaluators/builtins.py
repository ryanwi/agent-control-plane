"""Built-in evaluator implementations."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from agent_control_plane.types.policies import PolicySnapshot
from agent_control_plane.types.proposals import ActionProposal

from .protocol import EvaluatorResult


class RegexEvaluatorConfig(BaseModel):
    """Configuration for regex-based evaluation."""

    patterns: list[str]
    field: str = "resource_id"
    deny_on_match: bool = True


class RegexEvaluator:
    """Evaluates proposal fields against regex patterns."""

    def __init__(self, config: RegexEvaluatorConfig) -> None:
        self._config = config
        self._compiled = [re.compile(p) for p in config.patterns]

    @property
    def name(self) -> str:
        return "regex"

    @property
    def config_schema(self) -> type[BaseModel] | None:
        return RegexEvaluatorConfig

    async def evaluate(self, proposal: ActionProposal, policy: PolicySnapshot) -> EvaluatorResult:
        value = str(getattr(proposal, self._config.field, ""))
        matched = any(p.search(value) for p in self._compiled)
        if matched and self._config.deny_on_match:
            return EvaluatorResult(allow=False, reason=f"Regex match on {self._config.field}: {value}")
        if not matched and not self._config.deny_on_match:
            return EvaluatorResult(allow=False, reason=f"No regex match on {self._config.field}: {value}")
        return EvaluatorResult(allow=True, reason="Regex check passed")


class ListEvaluatorConfig(BaseModel):
    """Configuration for list-based evaluation."""

    allowlist: list[str] = Field(default_factory=list)
    blocklist: list[str] = Field(default_factory=list)
    field: str = "decision"


class ListEvaluator:
    """Evaluates proposal fields against allow/block lists. Blocklist takes priority."""

    def __init__(self, config: ListEvaluatorConfig) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "list"

    @property
    def config_schema(self) -> type[BaseModel] | None:
        return ListEvaluatorConfig

    async def evaluate(self, proposal: ActionProposal, policy: PolicySnapshot) -> EvaluatorResult:
        value = str(getattr(proposal, self._config.field, ""))
        normalized = value.strip().lower()

        if normalized in {b.strip().lower() for b in self._config.blocklist}:
            return EvaluatorResult(allow=False, reason=f"Value in blocklist: {value}")

        if self._config.allowlist and normalized not in {a.strip().lower() for a in self._config.allowlist}:
            return EvaluatorResult(allow=False, reason=f"Value not in allowlist: {value}")

        return EvaluatorResult(allow=True, reason="List check passed")
