"""Session state integrity validation — invariant checks run before resuming or recovering a session."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from agent_control_plane.types.enums import SessionStatus

if TYPE_CHECKING:
    from agent_control_plane.types.sessions import SessionState


@dataclass(frozen=True)
class IntegrityViolation:
    """A single violated session-state invariant."""

    code: str
    message: str


class SessionStateIntegrityError(RuntimeError):
    """Raised when persisted session state fails invariant checks before a resume or recovery."""

    def __init__(self, violations: list[IntegrityViolation]) -> None:
        codes = ", ".join(v.code for v in violations)
        super().__init__(f"Session state integrity violations: {codes}")
        self.violations = violations


def validate_session_integrity(state: SessionState) -> list[IntegrityViolation]:
    """Return all invariant violations found in *state*, or an empty list if clean."""
    violations: list[IntegrityViolation] = []

    if state.used_cost < Decimal(0):
        violations.append(IntegrityViolation("negative_used_cost", f"used_cost is negative: {state.used_cost}"))
    if state.used_action_count < 0:
        msg = f"used_action_count is negative: {state.used_action_count}"
        violations.append(IntegrityViolation("negative_used_action_count", msg))
    if state.max_cost < Decimal(0):
        violations.append(IntegrityViolation("negative_max_cost", f"max_cost is negative: {state.max_cost}"))
    if state.max_action_count < 0:
        violations.append(
            IntegrityViolation("negative_max_action_count", f"max_action_count is negative: {state.max_action_count}")
        )
    if state.status == SessionStatus.ABORTED and state.abort_reason is None:
        violations.append(IntegrityViolation("aborted_without_reason", "session is ABORTED but abort_reason is None"))

    return violations
