"""Shared governance helpers for ACP integration demos."""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from agent_control_plane.sync import ControlPlaneFacade
from agent_control_plane.types.enums import ApprovalDecisionType, EventKind
from agent_control_plane.types.proposals import ActionProposal


class GovernanceDecision(StrEnum):
    APPROVE = "APPROVE"
    DENY = "DENY"


def parse_governance_decision(text: str) -> GovernanceDecision:
    normalized = text.strip().upper()
    if GovernanceDecision.APPROVE.value in normalized:
        return GovernanceDecision.APPROVE
    return GovernanceDecision.DENY


def fallback_decision_from_priority(priority: str) -> GovernanceDecision:
    return GovernanceDecision.DENY if priority.lower() == "high" else GovernanceDecision.APPROVE


def apply_governance_decision(
    *,
    cp: ControlPlaneFacade,
    session_id: UUID,
    proposal: ActionProposal,
    ticket_id: UUID,
    decision: GovernanceDecision,
    provider: str,
    decided_by: str,
    agent_id: str,
    reason: str,
    command_prefix: str,
) -> str:
    if decision is GovernanceDecision.APPROVE:
        cp.approve_ticket(
            ticket_id,
            decided_by=decided_by,
            reason=reason,
            decision_type=ApprovalDecisionType.ALLOW_ONCE,
            command_id=f"{command_prefix}-approve",
        )
        cp.emit(
            session_id,
            EventKind.APPROVAL_GRANTED,
            {"case_id": proposal.resource_id, "decision": decision.value, "provider": provider},
            state_bearing=True,
            command_id=f"{command_prefix}-emit-granted",
        )
        cp.emit(
            session_id,
            EventKind.EXECUTION_COMPLETED,
            {"case_id": proposal.resource_id, "result": "status sent"},
            state_bearing=True,
            agent_id=agent_id,
            command_id=f"{command_prefix}-emit-executed",
        )
        return "APPROVED"

    cp.deny_ticket(
        ticket_id,
        reason=reason,
        command_id=f"{command_prefix}-deny",
    )
    cp.emit(
        session_id,
        EventKind.APPROVAL_DENIED,
        {"case_id": proposal.resource_id, "decision": decision.value, "provider": provider},
        state_bearing=True,
        agent_id=agent_id,
        command_id=f"{command_prefix}-emit-denied",
    )
    return "DENIED"
