"""Built-in evaluator implementations."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from typing import Any
from urllib.parse import urlsplit

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


_URL_RE = re.compile(r"https?://([^/\s\"'<>]+)", re.IGNORECASE)


def _iter_strings(value: object) -> Iterator[str]:
    """Yield every string leaf in a nested mapping/sequence structure.

    Mapping *keys* are screened as well as values: MCP tool output keys are strings that
    re-enter the agent context (and surface in the ``output_keys`` success-audit payload),
    so a malicious key must not bypass screening.
    """
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from _iter_strings(key)
            yield from _iter_strings(item)
    elif isinstance(value, list | tuple | set):
        for item in value:
            yield from _iter_strings(item)


class RegexResponseEvaluatorConfig(BaseModel):
    """Configuration for regex-based screening of tool output.

    Screening is always deny-on-match: a matched pattern (or a non-allowlisted outbound
    host) denies. There is no require-match mode — tool output is screened *for* danger,
    not validated *against* a required shape.
    """

    patterns: list[str] = Field(default_factory=list)
    url_allowlist: list[str] = Field(default_factory=list)


class RegexResponseEvaluator:
    """Screens tool *output* for injection/exfil markers and disallowed outbound URLs.

    Walks the string leaves of the returned output mapping. Denies (fail-closed) when any
    leaf matches a configured pattern (e.g. ``ignore (all )?previous instructions``,
    credential shapes), or when ``url_allowlist`` is set and a leaf contains a URL whose
    host is not allowlisted. The allowlist host check is the seam where first-class egress
    capability-grant modeling can later plug in.
    """

    def __init__(self, config: RegexResponseEvaluatorConfig) -> None:
        self._config = config
        self._compiled = [re.compile(p, re.IGNORECASE) for p in config.patterns]
        self._allowed_hosts = {h.strip().lower() for h in config.url_allowlist}

    @property
    def name(self) -> str:
        return "regex_response"

    @property
    def config_schema(self) -> type[BaseModel] | None:
        return RegexResponseEvaluatorConfig

    def evaluate_response(
        self, proposal: ActionProposal, output: Mapping[str, Any], policy: PolicySnapshot
    ) -> EvaluatorResult:
        for value in _iter_strings(output):
            for pattern in self._compiled:
                if pattern.search(value):
                    return EvaluatorResult(allow=False, reason=f"Output matched screening pattern: {pattern.pattern}")
            if self._allowed_hosts:
                for match in _URL_RE.finditer(value):
                    parsed = urlsplit(match.group(0))
                    host = (parsed.hostname or "").lower()
                    if not self._host_allowed(host):
                        return EvaluatorResult(allow=False, reason=f"Output references non-allowlisted host: {host}")
        return EvaluatorResult(allow=True, reason="Response screening passed")

    def _host_allowed(self, host: str) -> bool:
        return any(host == allowed or host.endswith(f".{allowed}") for allowed in self._allowed_hosts)
