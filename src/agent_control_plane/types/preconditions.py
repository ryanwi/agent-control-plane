"""Pre-execution precondition DTOs and provider protocol."""

from __future__ import annotations

import os
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
