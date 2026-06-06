"""Pre-execution precondition verification."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from uuid import UUID

from agent_control_plane.engine.event_store import EventStore
from agent_control_plane.types.enums import EventKind, GovernanceOutcome
from agent_control_plane.types.frames import EventMetadata
from agent_control_plane.types.preconditions import (
    EnvironmentVariableStateProvider,
    FileHashStateProvider,
    Precondition,
    PreconditionDivergence,
    PreconditionStateProvider,
    PreconditionStatus,
    PreconditionVerificationResult,
)


class PreconditionVerifier:
    """Checks declared resource state immediately before execution."""

    def __init__(
        self,
        event_store: EventStore | None,
        providers: Iterable[PreconditionStateProvider] | None = None,
    ) -> None:
        self._event_store = event_store
        builtins: tuple[PreconditionStateProvider, ...] = (
            FileHashStateProvider(),
            EnvironmentVariableStateProvider(),
        )
        self._providers = {provider.provider_id: provider for provider in (*builtins, *(providers or ()))}

    async def verify(
        self,
        session_id: UUID,
        preconditions: list[Precondition],
        *,
        proposal_id: UUID | None = None,
        action_id: str | None = None,
        metadata: EventMetadata | None = None,
    ) -> PreconditionVerificationResult:
        """Verify preconditions and append a state-bearing failure event on mismatch."""

        if self._event_store is None:
            raise RuntimeError("EventStore is required for PreconditionVerifier.verify(); use check() for pure checks")

        result = self.check(preconditions)
        if result.status != PreconditionStatus.FAILED:
            return result

        payload = precondition_failure_payload(result, proposal_id=proposal_id, action_id=action_id)

        await self._event_store.append(
            session_id=session_id,
            event_kind=EventKind.PRECONDITION_FAILED,
            payload=payload,
            state_bearing=True,
            metadata=metadata,
        )
        return result

    def check(self, preconditions: list[Precondition]) -> PreconditionVerificationResult:
        """Check preconditions without recording events."""

        if not preconditions:
            return PreconditionVerificationResult(status=PreconditionStatus.SKIPPED)

        divergences: list[PreconditionDivergence] = []
        for precondition in preconditions:
            divergence = self._check_one(precondition)
            if divergence is not None:
                divergences.append(divergence)

        if not divergences:
            return PreconditionVerificationResult(
                status=PreconditionStatus.PASSED,
                checked_count=len(preconditions),
            )
        return PreconditionVerificationResult(
            status=PreconditionStatus.FAILED,
            checked_count=len(preconditions),
            divergences=divergences,
        )

    def _check_one(self, precondition: Precondition) -> PreconditionDivergence | None:
        provider = self._providers.get(precondition.provider_id)
        if provider is None:
            return PreconditionDivergence(
                resource_id=precondition.resource_id,
                provider_id=precondition.provider_id,
                expected_state=precondition.expected_state,
                error=f"Unknown precondition provider: {precondition.provider_id}",
            )
        try:
            actual_state = provider.read_state(precondition)
        except Exception as exc:
            return PreconditionDivergence(
                resource_id=precondition.resource_id,
                provider_id=precondition.provider_id,
                expected_state=precondition.expected_state,
                error=str(exc),
            )
        if actual_state != precondition.expected_state:
            return PreconditionDivergence(
                resource_id=precondition.resource_id,
                provider_id=precondition.provider_id,
                expected_state=precondition.expected_state,
                actual_state=actual_state,
            )
        return None


def precondition_failure_payload(
    result: PreconditionVerificationResult,
    *,
    proposal_id: UUID | None = None,
    action_id: str | None = None,
) -> dict[str, Any]:
    """Build the canonical PRECONDITION_FAILED event payload."""

    payload: dict[str, Any] = {
        "status": result.status.value,
        "checked_count": result.checked_count,
        "divergences": [d.model_dump(mode="json") for d in result.divergences],
        "outcome": GovernanceOutcome.PRECONDITION_FAILED.value,
    }
    if proposal_id is not None:
        payload["proposal_id"] = str(proposal_id)
    if action_id is not None:
        payload["action_id"] = action_id
    return payload
