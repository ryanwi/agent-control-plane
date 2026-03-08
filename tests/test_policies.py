from __future__ import annotations

from uuid import uuid4

from agent_control_plane.policies import PassThroughGuardrailPolicy, ThresholdEvaluatorPolicy
from agent_control_plane.types.enums import EvaluationDecision, GuardrailPhase


def test_threshold_evaluator_policy_blocks_below_threshold() -> None:
    policy = ThresholdEvaluatorPolicy(threshold=0.75)
    result = policy.evaluate(
        session_id=uuid4(),
        operation="deploy",
        score=0.4,
        reasons=["risk"],
    )
    assert result.decision == EvaluationDecision.BLOCK
    assert result.reasons == ["risk"]


def test_threshold_evaluator_policy_passes_at_threshold() -> None:
    policy = ThresholdEvaluatorPolicy(threshold=0.75)
    result = policy.evaluate(
        session_id=uuid4(),
        operation="deploy",
        score=0.75,
        reasons=[],
    )
    assert result.decision == EvaluationDecision.PASS


def test_pass_through_guardrail_policy_allows_with_metadata_copy() -> None:
    policy = PassThroughGuardrailPolicy()
    metadata = {"source": "test"}
    result = policy.check(
        session_id=uuid4(),
        phase=GuardrailPhase.TOOL,
        policy_code="CP-GR-TEST",
        reason="allowed",
        metadata=metadata,
    )
    assert result.allow is True
    assert result.metadata == metadata
    assert result.phase == GuardrailPhase.TOOL
