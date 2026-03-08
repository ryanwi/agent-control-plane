"""Pluggable evaluator and guardrail policy interfaces."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from agent_control_plane.types.agentic import EvaluationResultDTO, GuardrailDecisionDTO
from agent_control_plane.types.enums import EvaluationDecision, GuardrailPhase


class EvaluatorPolicy(Protocol):
    def evaluate(
        self,
        *,
        session_id: UUID,
        operation: str,
        score: float,
        reasons: list[str],
    ) -> EvaluationResultDTO: ...


class GuardrailPolicy(Protocol):
    def check(
        self,
        *,
        session_id: UUID,
        phase: GuardrailPhase,
        policy_code: str,
        reason: str,
        metadata: dict[str, object] | None = None,
    ) -> GuardrailDecisionDTO: ...


class ThresholdEvaluatorPolicy:
    """Simple threshold-based evaluator policy."""

    def __init__(self, threshold: float = 0.5) -> None:
        self._threshold = threshold

    def evaluate(
        self,
        *,
        session_id: UUID,
        operation: str,
        score: float,
        reasons: list[str],
    ) -> EvaluationResultDTO:
        decision = EvaluationDecision.PASS if score >= self._threshold else EvaluationDecision.BLOCK
        return EvaluationResultDTO(
            session_id=session_id,
            operation=operation,
            decision=decision,
            score=score,
            reasons=reasons,
            actions=[],
        )


class PassThroughGuardrailPolicy:
    """Always allow guardrail policy useful for wiring and tests."""

    def check(
        self,
        *,
        session_id: UUID,
        phase: GuardrailPhase,
        policy_code: str,
        reason: str,
        metadata: dict[str, object] | None = None,
    ) -> GuardrailDecisionDTO:
        return GuardrailDecisionDTO(
            session_id=session_id,
            phase=phase,
            allow=True,
            policy_code=policy_code,
            reason=reason,
            metadata=dict(metadata or {}),
        )
