"""Polymorphic action-policy handlers and registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent_control_plane.types.enums import ActionName, ActionTier, ActionValue, RiskLevel, RoutingResolutionStep
from agent_control_plane.types.policies import PolicySnapshot
from agent_control_plane.types.proposals import ActionProposal

if TYPE_CHECKING:
    from agent_control_plane.types.steering import SteeringContext


@dataclass(frozen=True)
class RoutingReason:
    """The human-readable rationale and classification step for a routing decision."""

    reason: str
    resolution_step: RoutingResolutionStep


class ActionPolicyHandler(ABC):
    """Base abstraction for action-tier classification behavior."""

    @abstractmethod
    def classify_tier(
        self,
        proposal: ActionProposal,
        risk_level: RiskLevel,
        policy: PolicySnapshot,
        can_auto_approve: bool,
    ) -> ActionTier: ...

    @abstractmethod
    def build_routing_reason(
        self,
        proposal: ActionProposal,
        risk_level: RiskLevel,
        tier: ActionTier,
    ) -> RoutingReason: ...


class BlockedActionHandler(ActionPolicyHandler):
    def classify_tier(
        self,
        proposal: ActionProposal,
        risk_level: RiskLevel,
        policy: PolicySnapshot,
        can_auto_approve: bool,
    ) -> ActionTier:
        return ActionTier.BLOCKED

    def build_routing_reason(
        self,
        proposal: ActionProposal,
        risk_level: RiskLevel,
        tier: ActionTier,
    ) -> RoutingReason:
        return RoutingReason(
            reason=f"Action blocked by policy (resource={proposal.resource_id})",
            resolution_step=RoutingResolutionStep.EXPLICIT_ASSIGNMENT,
        )


class UnknownActionHandler(BlockedActionHandler):
    def build_routing_reason(
        self,
        proposal: ActionProposal,
        risk_level: RiskLevel,
        tier: ActionTier,
    ) -> RoutingReason:
        action_value = proposal.decision.value if isinstance(proposal.decision, ActionName) else proposal.decision
        return RoutingReason(
            reason=f"Unknown action blocked by policy (action={action_value})",
            resolution_step=RoutingResolutionStep.EXPLICIT_ASSIGNMENT,
        )


class AlwaysApproveActionHandler(ActionPolicyHandler):
    def classify_tier(
        self,
        proposal: ActionProposal,
        risk_level: RiskLevel,
        policy: PolicySnapshot,
        can_auto_approve: bool,
    ) -> ActionTier:
        return ActionTier.ALWAYS_APPROVE

    def build_routing_reason(
        self,
        proposal: ActionProposal,
        risk_level: RiskLevel,
        tier: ActionTier,
    ) -> RoutingReason:
        return RoutingReason(
            reason=f"{risk_level.value.upper()} risk requires human approval",
            resolution_step=RoutingResolutionStep.POLICY_LIST_MATCH,
        )


class AutoApproveActionHandler(ActionPolicyHandler):
    def classify_tier(
        self,
        proposal: ActionProposal,
        risk_level: RiskLevel,
        policy: PolicySnapshot,
        can_auto_approve: bool,
    ) -> ActionTier:
        return ActionTier.AUTO_APPROVE if can_auto_approve else ActionTier.ALWAYS_APPROVE

    def build_routing_reason(
        self,
        proposal: ActionProposal,
        risk_level: RiskLevel,
        tier: ActionTier,
    ) -> RoutingReason:
        if tier == ActionTier.AUTO_APPROVE:
            return RoutingReason(
                reason=f"Policy list auto-approve (score={proposal.score}, weight={proposal.weight})",
                resolution_step=RoutingResolutionStep.POLICY_LIST_MATCH,
            )
        return RoutingReason(
            reason="Auto-approve disabled by policy constraints; requires human approval",
            resolution_step=RoutingResolutionStep.POLICY_LIST_MATCH,
        )


class SteeringActionHandler(ActionPolicyHandler):
    """Steers the agent toward alternative actions instead of blocking."""

    def classify_tier(
        self,
        proposal: ActionProposal,
        risk_level: RiskLevel,
        policy: PolicySnapshot,
        can_auto_approve: bool,
    ) -> ActionTier:
        return ActionTier.STEER

    def build_routing_reason(
        self,
        proposal: ActionProposal,
        risk_level: RiskLevel,
        tier: ActionTier,
    ) -> RoutingReason:
        return RoutingReason(
            reason=f"Action steered by policy (resource={proposal.resource_id})",
            resolution_step=RoutingResolutionStep.POLICY_LIST_MATCH,
        )

    def build_steering_context(
        self,
        proposal: ActionProposal,
        _risk_level: RiskLevel,
        policy: PolicySnapshot,
    ) -> SteeringContext:
        from agent_control_plane.types.steering import SteeringContext

        suggested = list(policy.action_tiers.auto_approve) + list(policy.action_tiers.unrestricted)
        action_label = proposal.decision.value if isinstance(proposal.decision, ActionName) else proposal.decision
        if suggested:
            alternatives = ", ".join(str(a) for a in suggested)
            guidance = f"Action '{action_label}' requires steering. Consider alternatives: {alternatives}"
        else:
            guidance = f"Action '{action_label}' requires steering. No pre-approved alternatives available."
        return SteeringContext(
            guidance=guidance,
            suggested_actions=suggested,
        )


class DefaultRiskBasedHandler(ActionPolicyHandler):
    def classify_tier(
        self,
        proposal: ActionProposal,
        risk_level: RiskLevel,
        policy: PolicySnapshot,
        can_auto_approve: bool,
    ) -> ActionTier:
        if risk_level == RiskLevel.LOW:
            return ActionTier.AUTO_APPROVE if can_auto_approve else ActionTier.ALWAYS_APPROVE
        return ActionTier.ALWAYS_APPROVE

    def build_routing_reason(
        self,
        proposal: ActionProposal,
        risk_level: RiskLevel,
        tier: ActionTier,
    ) -> RoutingReason:
        if tier == ActionTier.AUTO_APPROVE:
            return RoutingReason(
                reason=f"LOW risk auto-approve (score={proposal.score}, weight={proposal.weight})",
                resolution_step=RoutingResolutionStep.RISK_TIER_MATCH,
            )
        return RoutingReason(
            reason=f"{risk_level.value.upper()} risk requires human approval",
            resolution_step=RoutingResolutionStep.RISK_TIER_MATCH,
        )


class ActionPolicyRegistry:
    """Maps actions to concrete policy handlers."""

    def __init__(self, policy: PolicySnapshot) -> None:
        self._unknown_handler = UnknownActionHandler()
        self._default_handler = DefaultRiskBasedHandler()
        self._handlers_by_action: dict[ActionValue, ActionPolicyHandler] = {}

        for action in policy.action_tiers.auto_approve:
            self._handlers_by_action[action] = AutoApproveActionHandler()
        for action in policy.action_tiers.always_approve:
            self._handlers_by_action[action] = AlwaysApproveActionHandler()
        for action in policy.action_tiers.steer:
            self._handlers_by_action[action] = SteeringActionHandler()
        for action in policy.action_tiers.blocked:
            self._handlers_by_action[action] = BlockedActionHandler()

    def resolve(self, action: ActionValue) -> ActionPolicyHandler:
        if action == ActionName.UNKNOWN:
            return self._unknown_handler
        return self._handlers_by_action.get(action, self._default_handler)
