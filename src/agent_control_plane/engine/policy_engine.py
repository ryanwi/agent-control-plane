"""Action classification, risk tiering, and asset scope enforcement."""

import logging
from decimal import Decimal
from typing import Protocol

from agent_control_plane.engine.action_policy import ActionPolicyHandler, ActionPolicyRegistry
from agent_control_plane.types.enums import (
    ActionName,
    ActionTier,
    AssetMatch,
    AssetScope,
    ExecutionMode,
    RiskLevel,
    RoutingResolutionStep,
)
from agent_control_plane.types.policies import PolicySnapshot
from agent_control_plane.types.proposals import ActionProposal

logger = logging.getLogger(__name__)


class AssetClassifier(Protocol):
    """Protocol for classifying assets by resource ID."""

    def classify(self, resource_id: str) -> AssetMatch: ...


class RiskClassifier(Protocol):
    """Protocol for classifying proposal risk level.

    Implement this to provide domain-specific risk classification.
    The default implementation uses proposal weight and score fields.
    """

    def classify(self, proposal: ActionProposal, policy: PolicySnapshot) -> RiskLevel: ...


class DefaultAssetClassifier:
    """Default implementation using pattern matching."""

    def __init__(self, patterns: frozenset[str] | None = None) -> None:
        self._patterns = patterns or frozenset()

    def classify(self, resource_id: str) -> AssetMatch:
        upper = resource_id.upper()
        if any(p in upper for p in self._patterns):
            return AssetMatch.MATCHED
        return AssetMatch.UNMATCHED


class DefaultRiskClassifier:
    """Default risk classifier using weight and score thresholds.

    LOW: Asset matches classifier + weight <= max + score >= min
    HIGH: Weight >= max_weight_pct OR score < 0.5
    MEDIUM: Everything else
    """

    def __init__(self, asset_classifier: AssetClassifier | None = None) -> None:
        self._asset_classifier = asset_classifier

    def classify(self, proposal: ActionProposal, policy: PolicySnapshot) -> RiskLevel:
        is_matched = self._is_matched_asset(proposal.resource_id)
        auto_cond = policy.auto_approve_conditions

        # LOW risk if asset matches AND (weight/score are within auto-approve bounds)
        if is_matched and proposal.weight <= auto_cond.max_weight and proposal.score >= auto_cond.min_score:
            return RiskLevel.LOW

        # HIGH risk if weight exceeds global policy limit OR score is very low
        if proposal.weight >= policy.risk_limits.max_weight_pct or proposal.score < Decimal("0.5"):
            return RiskLevel.HIGH

        return RiskLevel.MEDIUM

    def _is_matched_asset(self, resource_id: str) -> bool:
        if self._asset_classifier is None:
            return True
        return self._asset_classifier.classify(resource_id) == AssetMatch.MATCHED


class PolicyEngine:
    """Classifies proposals by risk tier and enforces policy constraints."""

    def __init__(
        self,
        policy: PolicySnapshot,
        asset_classifier: AssetClassifier | None = None,
        risk_classifier: RiskClassifier | None = None,
    ) -> None:
        self.policy = policy
        self._asset_classifier = asset_classifier
        self._risk_classifier = risk_classifier or DefaultRiskClassifier(asset_classifier)
        self._action_registry = ActionPolicyRegistry(policy)

    def classify_risk_level(self, proposal: ActionProposal) -> RiskLevel:
        """Classify a proposal's risk level using the configured risk classifier."""
        return self._risk_classifier.classify(proposal, self.policy)

    def classify_action_tier(
        self,
        proposal: ActionProposal,
        risk_level: RiskLevel,
    ) -> ActionTier:
        """Determine the action tier for a proposal.

        Resolution order (deterministic, logged):
        1. explicit_assignment - blocked actions check
        2. policy_list_match - always_approve or auto_approve lists
        3. risk_tier_match - risk level maps to tier
        4. capability_match - asset scope enforcement
        5. default_agent - ALWAYS_APPROVE
        """
        # 1. Check if action is blocked
        if self._is_blocked(proposal):
            logger.info(
                "Proposal %s BLOCKED by policy (resource=%s)",
                proposal.id,
                proposal.resource_id,
            )
            return ActionTier.BLOCKED

        # 2. Asset scope enforcement
        if not self._passes_asset_scope(proposal):
            logger.info(
                "Proposal %s BLOCKED by asset scope (resource=%s, scope=%s)",
                proposal.id,
                proposal.resource_id,
                self.policy.asset_scope,
            )
            return ActionTier.BLOCKED

        handler = self.get_action_handler(proposal)
        return handler.classify_tier(
            proposal=proposal,
            risk_level=risk_level,
            policy=self.policy,
            can_auto_approve=self._can_auto_approve(risk_level),
        )

    def get_action_handler(self, proposal: ActionProposal) -> ActionPolicyHandler:
        """Resolve the action handler for a proposal."""
        return self._action_registry.resolve(proposal.decision)

    def build_routing_reason(
        self,
        proposal: ActionProposal,
        risk_level: RiskLevel,
        tier: ActionTier,
    ) -> tuple[str, RoutingResolutionStep]:
        """Build routing reason + resolution step via polymorphic handlers."""
        if not self._passes_asset_scope(proposal):
            return (
                f"Action blocked by asset scope (resource={proposal.resource_id}, scope={self.policy.asset_scope})",
                RoutingResolutionStep.CAPABILITY_MATCH,
            )
        handler = self.get_action_handler(proposal)
        return handler.build_routing_reason(proposal, risk_level, tier)

    def _is_blocked(self, proposal: ActionProposal) -> bool:
        """Check if the proposal's action is in the blocked list."""
        return proposal.decision == ActionName.UNKNOWN or proposal.decision in self.policy.action_tiers.blocked

    def _passes_asset_scope(self, proposal: ActionProposal) -> bool:
        """Check if the proposal passes the asset scope filter."""
        if self.policy.asset_scope == AssetScope.MATCHED_ONLY:
            return self._is_matched_asset(proposal.resource_id)
        return True

    def _can_auto_approve(self, risk_level: RiskLevel) -> bool:
        """Check if auto-approval is allowed by policy for this risk level."""
        auto_cond = self.policy.auto_approve_conditions

        # 1. Mode check
        if auto_cond.dry_run_only and self.policy.execution_mode != ExecutionMode.DRY_RUN:
            return False

        # 2. Risk tier check (using comparable enum)
        return risk_level <= auto_cond.max_risk_tier

    def _is_matched_asset(self, resource_id: str) -> bool:
        """Check if a resource matches the configured asset classifier."""
        if self._asset_classifier is None:
            return True
        return self._asset_classifier.classify(resource_id) == AssetMatch.MATCHED
