"""Deterministic proposal routing to action tiers."""

import logging
from dataclasses import dataclass

from agent_control_plane.engine.policy_engine import PolicyEngine
from agent_control_plane.types.enums import ActionTier, RiskLevel
from agent_control_plane.types.proposals import ActionProposalDTO

logger = logging.getLogger(__name__)


@dataclass
class RoutingDecision:
    """Result of routing a proposal through the policy engine."""

    tier: ActionTier
    risk_level: RiskLevel
    reason: str
    resolution_step: str  # explicit_assignment, risk_tier_match, capability_match, default_agent


class ProposalRouter:
    """Routes proposals through the policy engine with full audit trail."""

    def __init__(self, policy_engine: PolicyEngine) -> None:
        self.policy_engine = policy_engine

    def route(self, proposal: ActionProposalDTO) -> RoutingDecision:
        """Route a proposal and return the decision with audit trail."""
        risk_level = self.policy_engine.classify_risk_level(proposal)
        tier = self.policy_engine.classify_action_tier(proposal, risk_level)

        # Determine which step resolved the decision
        if tier == ActionTier.BLOCKED:
            resolution = "explicit_assignment"
            reason = f"Action blocked by policy (resource={proposal.resource_id})"
        elif tier == ActionTier.AUTO_APPROVE:
            resolution = "risk_tier_match"
            reason = f"LOW risk auto-approve (confidence={proposal.confidence}, allocation={proposal.allocation_pct}%)"
        elif tier == ActionTier.ALWAYS_APPROVE:
            resolution = "risk_tier_match"
            reason = f"{risk_level.value.upper()} risk requires human approval"
        else:
            resolution = "default_agent"
            reason = "Default routing"

        decision = RoutingDecision(
            tier=tier,
            risk_level=risk_level,
            reason=reason,
            resolution_step=resolution,
        )

        logger.info(
            "Routed proposal %s -> %s (risk=%s, step=%s)",
            proposal.id,
            tier.value,
            risk_level.value,
            resolution,
        )
        return decision
