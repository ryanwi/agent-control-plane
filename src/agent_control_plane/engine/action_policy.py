"""Polymorphic action-policy handlers and registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from agent_control_plane.types.enums import ActionName, ActionTier, ActionValue, RiskLevel, RoutingResolutionStep
from agent_control_plane.types.policies import PolicySnapshot
from agent_control_plane.types.proposals import ActionProposal

if TYPE_CHECKING:
    from agent_control_plane.types.steering import SteeringContext


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
    ) -> tuple[str, RoutingResolutionStep]: ...


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
    ) -> tuple[str, RoutingResolutionStep]:
        return (
            f"Action blocked by policy (resource={proposal.resource_id})",
            RoutingResolutionStep.EXPLICIT_ASSIGNMENT,
        )


class UnknownActionHandler(BlockedActionHandler):
    def build_routing_reason(
        self,
        proposal: ActionProposal,
        risk_level: RiskLevel,
        tier: ActionTier,
    ) -> tuple[str, RoutingResolutionStep]:
        action_value = proposal.decision.value if isinstance(proposal.decision, ActionName) else proposal.decision
        return (
            f"Unknown action blocked by policy (action={action_value})",
            RoutingResolutionStep.EXPLICIT_ASSIGNMENT,
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
    ) -> tuple[str, RoutingResolutionStep]:
        return (
            f"{risk_level.value.upper()} risk requires human approval",
            RoutingResolutionStep.POLICY_LIST_MATCH,
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
    ) -> tuple[str, RoutingResolutionStep]:
        if tier == ActionTier.AUTO_APPROVE:
            return (
                f"Policy list auto-approve (score={proposal.score}, weight={proposal.weight})",
                RoutingResolutionStep.POLICY_LIST_MATCH,
            )
        return (
            "Auto-approve disabled by policy constraints; requires human approval",
            RoutingResolutionStep.POLICY_LIST_MATCH,
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
    ) -> tuple[str, RoutingResolutionStep]:
        return (
            f"Action steered by policy (resource={proposal.resource_id})",
            RoutingResolutionStep.POLICY_LIST_MATCH,
        )

    def build_steering_context(
        self,
        proposal: ActionProposal,
        risk_level: RiskLevel,
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
    ) -> tuple[str, RoutingResolutionStep]:
        if tier == ActionTier.AUTO_APPROVE:
            return (
                f"LOW risk auto-approve (score={proposal.score}, weight={proposal.weight})",
                RoutingResolutionStep.RISK_TIER_MATCH,
            )
        return (
            f"{risk_level.value.upper()} risk requires human approval",
            RoutingResolutionStep.RISK_TIER_MATCH,
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
