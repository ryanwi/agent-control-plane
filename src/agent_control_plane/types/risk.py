"""Session-level risk accumulation DTOs."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel

from agent_control_plane.types.enums import RiskLevel


class RiskPattern(BaseModel):
    """An ordered action sequence that, when detected, escalates session risk."""

    name: str
    description: str
    action_sequence: list[str]  # ordered contiguous match
    window_size: int = 10  # how many recent actions to look back
    escalate_to: RiskLevel


class SessionRiskState(BaseModel):
    """Accumulated risk state for a single session."""

    session_id: UUID
    accumulated_score: Decimal
    action_count: int
    recent_actions: list[str]  # sliding window capped at max pattern window_size
    detected_patterns: list[str]
    current_risk_level: RiskLevel


class SessionRiskEscalation(BaseModel):
    """Result of a single risk assessment, including any escalation decision."""

    original_risk: RiskLevel
    escalated_risk: RiskLevel
    escalation_reasons: list[str]
    session_state: SessionRiskState
    was_escalated: bool
