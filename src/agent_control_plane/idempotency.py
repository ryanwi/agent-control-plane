"""Helpers for deterministic command-id generation."""

from __future__ import annotations

from hashlib import sha256
from typing import Any
from uuid import UUID

from agent_control_plane.types.enums import ActionValue
from agent_control_plane.types.ids import IdempotencyKey


def proposal_command_id(
    *,
    session_id: UUID,
    resource_id: str,
    resource_type: str,
    decision: ActionValue,
    namespace: str = "proposal:create",
) -> IdempotencyKey:
    """Build a stable idempotency key for proposal creation retries."""
    decision_value = decision.value if hasattr(decision, "value") else str(decision)
    parts: list[Any] = [
        namespace,
        str(session_id),
        resource_id,
        resource_type,
        decision_value,
    ]
    digest = sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:24]
    return IdempotencyKey(f"{namespace}:{digest}")
