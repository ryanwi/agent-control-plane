"""Deterministic proposal routing to action tiers."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from agent_control_plane.engine.policy_engine import PolicyEngine
from agent_control_plane.types.enums import ActionTier, RiskLevel, RoutingResolutionStep
from agent_control_plane.types.proposals import ActionProposal
from agent_control_plane.types.risk import SessionRiskEscalation
from agent_control_plane.types.steering import SteeringContext

if TYPE_CHECKING:
    from agent_control_plane.engine.agent_registry import AgentRegistry
    from agent_control_plane.engine.session_risk_accumulator import SessionRiskAccumulator

logger = logging.getLogger(__name__)


@dataclass
class RoutingDecision:
    """Result of routing a proposal through the policy engine."""

    tier: ActionTier
    risk_level: RiskLevel
    reason: str
    resolution_step: RoutingResolutionStep
    steering: SteeringContext | None = field(default=None)
    risk_escalated: bool = False
    risk_escalation: SessionRiskEscalation | None = None


class ProposalRouter:
    """Routes proposals through the policy engine with full audit trail."""

    def __init__(
        self,
        policy_engine: PolicyEngine,
        agent_registry: AgentRegistry | None = None,
        risk_accumulator: SessionRiskAccumulator | None = None,
    ) -> None:
        self.policy_engine = policy_engine
        self.agent_registry = agent_registry
        self.risk_accumulator = risk_accumulator

    async def route(self, proposal: ActionProposal) -> RoutingDecision:
        """Route a proposal and return the decision with audit trail."""
        # 1. Identity Check
        if self.agent_registry and proposal.agent_id:
            agent = await self.agent_registry.get_agent(proposal.agent_id)
            if not agent:
                logger.warning("Proposal from unregistered agent: %s", proposal.agent_id)
            elif await self.agent_registry.is_revoked(proposal.session_id, proposal.agent_id):
                # Per-session revocation fails closed, regardless of the agent's capabilities.
                logger.warning("Agent %s is revoked for session %s — blocking", proposal.agent_id, proposal.session_id)
                return RoutingDecision(
                    tier=ActionTier.BLOCKED,
                    risk_level=self.policy_engine.classify_risk_level(proposal),
                    reason=f"Agent {proposal.agent_id} is revoked for session {proposal.session_id}",
                    resolution_step=RoutingResolutionStep.CAPABILITY_MATCH,
                    steering=None,
                )
            else:
                # Validate against the agent's own capabilities only (delegation/handoff
                # records never elevate this — see AgentMetadata.is_capable).
                capable = agent.is_capable(proposal.decision)
                if not capable:
                    logger.warning(
                        "Agent %s is not registered for action %s",
                        proposal.agent_id,
                        proposal.decision,
                    )

        original_risk_level = self.policy_engine.classify_risk_level(proposal)
        risk_escalation = None
        risk_level = original_risk_level
        if self.risk_accumulator is not None:
            risk_escalation = await self.risk_accumulator.assess(proposal.session_id, proposal, original_risk_level)
            risk_level = risk_escalation.escalated_risk

        can_auto = await self.policy_engine.can_auto_approve_with_tree(proposal, risk_level)
        tier = self.policy_engine.classify_action_tier(proposal, risk_level, can_auto_approve=can_auto)

        routing = self.policy_engine.build_routing_reason(proposal, risk_level, tier)

        steering = None
        if tier == ActionTier.STEER:
            from agent_control_plane.engine.action_policy import SteeringActionHandler

            handler = self.policy_engine.get_action_handler(proposal)
            if isinstance(handler, SteeringActionHandler):
                steering = handler.build_steering_context(proposal, risk_level, self.policy_engine.policy)

        decision = RoutingDecision(
            tier=tier,
            risk_level=risk_level,
            reason=routing.reason,
            resolution_step=routing.resolution_step,
            steering=steering,
            risk_escalated=risk_escalation.was_escalated if risk_escalation is not None else False,
            risk_escalation=risk_escalation,
        )

        logger.info(
            "Routed proposal %s -> %s (risk=%s, step=%s)",
            proposal.id,
            tier.value,
            risk_level.value,
            routing.resolution_step,
        )
        return decision
