"""Tests for comparable enums."""

from agent_control_plane.types.enums import RiskLevel


def test_risk_level_comparison():
    assert RiskLevel.LOW < RiskLevel.MEDIUM
    assert RiskLevel.MEDIUM < RiskLevel.HIGH
    assert RiskLevel.LOW < RiskLevel.HIGH

    assert RiskLevel.HIGH > RiskLevel.MEDIUM
    assert RiskLevel.MEDIUM > RiskLevel.LOW

    assert RiskLevel.LOW <= RiskLevel.LOW
    assert RiskLevel.LOW <= RiskLevel.MEDIUM

    assert RiskLevel.HIGH >= RiskLevel.HIGH
    assert RiskLevel.HIGH >= RiskLevel.LOW

    # Equality check (inherited from StrEnum)
    assert RiskLevel.LOW == "low"
    assert RiskLevel.LOW == RiskLevel.LOW
