"""Pre-execution precondition DTOs and provider protocol."""

from __future__ import annotations

import os
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

PRECONDITIONS_METADATA_KEY = "__acp_preconditions"


class PreconditionStatus(StrEnum):
    """Pre-execution verification status."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class Precondition(BaseModel):
    """Expected state for a resource immediately before execution."""

    resource_id: str
    expected_state: Any
    provider_id: str = "default"
    metadata: dict[str, Any] = Field(default_factory=dict)


class FreshnessPrecondition(Precondition):
    """Precondition for resource freshness (TTL)."""

    provider_id: str = "freshness"


class ConsensusPrecondition(Precondition):
    """Precondition for multi-source consensus mismatch check."""

    provider_id: str = "consensus"


class PreconditionDivergence(BaseModel):
    """Observed mismatch between expected and actual resource state."""

    resource_id: str
    provider_id: str
    expected_state: Any
    actual_state: Any = None
    error: str | None = None


class PreconditionVerificationResult(BaseModel):
    """Result of checking a proposal's preconditions."""

    status: PreconditionStatus
    checked_count: int = 0
    divergences: list[PreconditionDivergence] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.status in {PreconditionStatus.PASSED, PreconditionStatus.SKIPPED}


@runtime_checkable
class PreconditionStateProvider(Protocol):
    """Reads current resource state for a precondition."""

    provider_id: str

    def read_state(self, precondition: Precondition) -> Any: ...


class FileHashStateProvider:
    """SHA-256 file-content state provider."""

    provider_id = "file_sha256"

    def read_state(self, precondition: Precondition) -> str:
        import hashlib

        path = Path(precondition.resource_id)
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()


class EnvironmentVariableStateProvider:
    """Environment-variable value state provider."""

    provider_id = "env"

    def read_state(self, precondition: Precondition) -> str | None:
        return os.environ.get(precondition.resource_id)


class FreshnessStateProvider:
    """Checks a resource's metadata/timestamp for decay."""

    provider_id = "freshness"

    def __init__(self, get_timestamp_fn: Callable[[str], float | None] | None = None) -> None:
        self.get_timestamp_fn = get_timestamp_fn

    def read_state(self, precondition: Precondition) -> str:
        import time

        now = time.time()
        max_age = precondition.metadata.get("max_age_seconds", 60.0)

        resource_time = None
        if self.get_timestamp_fn is not None:
            resource_time = self.get_timestamp_fn(precondition.resource_id)

        if resource_time is None:
            resource_time = precondition.metadata.get("resource_timestamp")

        if resource_time is None:
            raise ValueError("No timestamp available for resource")

        if now - resource_time > max_age:
            return "stale_context"
        return "fresh"


class ConsensusStateProvider:
    """Checks for agreement among multiple state providers."""

    provider_id = "consensus"

    def __init__(self, providers: list[PreconditionStateProvider]) -> None:
        self.providers = providers

    def read_state(self, precondition: Precondition) -> Any:
        results = []
        for provider in self.providers:
            sub_precond = Precondition(
                resource_id=precondition.resource_id,
                expected_state=precondition.expected_state,
                provider_id=provider.provider_id,
                metadata=precondition.metadata,
            )
            try:
                val = provider.read_state(sub_precond)
                results.append(val)
            except Exception as exc:
                results.append(f"error: {exc}")

        if not results:
            raise ValueError("No providers configured for consensus")

        if len(set(results)) > 1:
            return "conflicting_context"
        return results[0]


def encode_preconditions_for_metadata(
    metadata: dict[str, Any],
    preconditions: list[Precondition],
) -> dict[str, Any]:
    """Return metadata with preconditions encoded under the reserved ACP key."""

    encoded = dict(metadata)
    if PRECONDITIONS_METADATA_KEY in encoded:
        raise ValueError(f"Reserved metadata key is managed by ACP: {PRECONDITIONS_METADATA_KEY}")
    if preconditions:
        encoded[PRECONDITIONS_METADATA_KEY] = [p.model_dump(mode="json") for p in preconditions]
    return encoded


def decode_preconditions_from_metadata(
    metadata: dict[str, Any],
) -> tuple[dict[str, Any], list[Precondition]]:
    """Split host metadata from ACP's reserved precondition payload."""

    public_metadata = dict(metadata)
    raw = public_metadata.pop(PRECONDITIONS_METADATA_KEY, [])
    if not raw:
        return public_metadata, []
    return public_metadata, [Precondition.model_validate(item) for item in raw]
