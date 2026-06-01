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


# Match absolute (http/https) and protocol-relative (//host) URLs so exfil destinations
# cannot bypass output screening by dropping the scheme.
_URL_RE = re.compile(r"(?:https?:)?//([^/\s\"'<>]+)", re.IGNORECASE)


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
        self._allowed_suffixes = {f".{h}" for h in self._allowed_hosts}

    @property
    def name(self) -> str:
        return "regex_response"

    @property
    def config_schema(self) -> type[BaseModel] | None:
        return RegexResponseEvaluatorConfig

    def evaluate_response(
        self, proposal: ActionProposal, output: Mapping[str, Any], policy: PolicySnapshot
    ) -> EvaluatorResult:
        check_patterns = bool(self._compiled)
        check_urls = bool(self._allowed_hosts)
        for value in _iter_strings(output):
            if check_patterns:
                for pattern in self._compiled:
                    if pattern.search(value):
                        reason = f"Output matched screening pattern: {pattern.pattern}"
                        return EvaluatorResult(allow=False, reason=reason)
            if check_urls:
                for match in _URL_RE.finditer(value):
                    host = (urlsplit(match.group(0)).hostname or "").lower()
                    if not self._host_allowed(host):
                        return EvaluatorResult(allow=False, reason=f"Output references non-allowlisted host: {host}")
        return EvaluatorResult(allow=True, reason="Response screening passed")

    def _host_allowed(self, host: str) -> bool:
        return host in self._allowed_hosts or any(host.endswith(s) for s in self._allowed_suffixes)


def _extract_host(value: str) -> str:
    """Extract a bare hostname from a destination that may be a URL, host:port, or host/path."""
    v = value.strip()
    # Prepend // only when the value has neither a scheme nor a protocol-relative prefix, so
    # urlsplit always has a netloc to parse (and a leading // is not doubled into ////).
    if "://" not in v and not v.startswith("//"):
        v = f"//{v}"
    return (urlsplit(v).hostname or "").lower()


class EgressGrant(BaseModel):
    """A single egress capability grant: a destination and the capabilities permitted there."""

    destination: str
    capabilities: list[str] = Field(default_factory=list)


class EgressEvaluatorConfig(BaseModel):
    """Configuration for the egress capability-grant evaluator.

    ``destination_field`` and ``capability_field`` name the proposal attributes that carry,
    respectively, the egress destination (a host or URL) and the capability being exercised
    there (an action/operation name).
    """

    grants: list[EgressGrant] = Field(default_factory=list)
    destination_field: str = "resource_id"
    capability_field: str = "decision"
    match_subdomains: bool = True


class EgressEvaluator:
    """Evaluates egress as a *capability grant*, not a destination filter.

    Reaching an allowlisted destination is necessary but not sufficient: the specific
    capability exercised at that destination must also be granted. This encodes the lesson
    that "every function reachable through a domain on an allowlist is an attack surface" —
    allowing ``api.anthropic.com`` for one operation must not implicitly allow every other
    operation reachable there. Fail-closed: an unknown destination, or a granted destination
    invoked with an ungranted capability, is denied.
    """

    def __init__(self, config: EgressEvaluatorConfig) -> None:
        self._config = config
        self._grants: dict[str, set[str]] = {}
        for grant in config.grants:
            # Normalize grant destinations to bare hosts so URL-form and host-form grants
            # both match host-normalized proposals.
            dest = _extract_host(grant.destination)
            caps = {c.strip().lower() for c in grant.capabilities}
            self._grants.setdefault(dest, set()).update(caps)
        self._subdomain_grants: list[tuple[str, set[str]]] = [(f".{dest}", caps) for dest, caps in self._grants.items()]

    @property
    def name(self) -> str:
        return "egress"

    @property
    def config_schema(self) -> type[BaseModel] | None:
        return EgressEvaluatorConfig

    async def evaluate(self, proposal: ActionProposal, policy: PolicySnapshot) -> EvaluatorResult:
        host = _extract_host(str(getattr(proposal, self._config.destination_field, "") or ""))
        capability = str(getattr(proposal, self._config.capability_field, "") or "").strip().lower()

        granted = self._match_destination(host)
        if granted is None:
            return EvaluatorResult(allow=False, reason=f"Destination not on egress allowlist: {host}")
        if capability not in granted:
            return EvaluatorResult(
                allow=False,
                reason=f"Destination {host} does not grant capability: {capability}",
            )
        return EvaluatorResult(allow=True, reason=f"Egress permitted: {capability} -> {host}")

    def _match_destination(self, host: str) -> set[str] | None:
        if host in self._grants:
            return self._grants[host]
        if self._config.match_subdomains:
            for suffix, caps in self._subdomain_grants:
                if host.endswith(suffix):
                    return caps
        return None
