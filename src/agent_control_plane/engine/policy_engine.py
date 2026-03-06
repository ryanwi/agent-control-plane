"""Action classification, risk tiering, and asset scope enforcement."""

import logging
from decimal import Decimal
from typing import Protocol

from agent_control_plane.types.enums import ActionTier, RiskLevel
from agent_control_plane.types.policies import PolicySnapshotDTO
from agent_control_plane.types.proposals import ActionProposalDTO

logger = logging.getLogger(__name__)


class AssetClassifier(Protocol):
    """Protocol for classifying assets by resource ID."""

    def classify(self, resource_id: str) -> str: ...


class RiskClassifier(Protocol):
    """Protocol for classifying proposal risk level.

    Implement this to provide domain-specific risk classification.
    The default implementation uses proposal weight and score fields.
    """

    def classify(self, proposal: ActionProposalDTO, policy: PolicySnapshotDTO) -> RiskLevel: ...


class DefaultAssetClassifier:
    """Default implementation using pattern matching."""

    def __init__(self, patterns: frozenset[str] | None = None) -> None:
        self._patterns = patterns or frozenset()

    def classify(self, resource_id: str) -> str:
        upper = resource_id.upper()
        if any(p in upper for p in self._patterns):
            return "matched"
        return "unmatched"


class DefaultRiskClassifier:
    """Default risk classifier using weight and score thresholds.

    LOW: Asset matches classifier + weight <= max + score >= min
    HIGH: Weight >= max_weight_pct OR score < 0.5
    MEDIUM: Everything else
    """

    def __init__(self, asset_classifier: AssetClassifier | None = None) -> None:
        self._asset_classifier = asset_classifier

    def classify(self, proposal: ActionProposalDTO, policy: PolicySnapshotDTO) -> RiskLevel:
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
        return self._asset_classifier.classify(resource_id) == "matched"


class PolicyEngine:
    """Classifies proposals by risk tier and enforces policy constraints."""

    def __init__(
        self,
        policy: PolicySnapshotDTO,
        asset_classifier: AssetClassifier | None = None,
        risk_classifier: RiskClassifier | None = None,
    ) -> None:
        self.policy = policy
        self._asset_classifier = asset_classifier
        self._risk_classifier = risk_classifier or DefaultRiskClassifier(asset_classifier)

    def classify_risk_level(self, proposal: ActionProposalDTO) -> RiskLevel:
        """Classify a proposal's risk level using the configured risk classifier."""
        return self._risk_classifier.classify(proposal, self.policy)

    def classify_action_tier(
        self,
        proposal: ActionProposalDTO,
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
        decision_str = str(proposal.decision).lower()

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

        # 3. Explicit Policy Lists
        always_approve = [a.lower() for a in self.policy.action_tiers.always_approve]
        auto_approve = [a.lower() for a in self.policy.action_tiers.auto_approve]

        if any(action in decision_str for action in always_approve):
            return ActionTier.ALWAYS_APPROVE

        # If in auto_approve list, it's a candidate for AUTO_APPROVE,
        # but it should still pass the global risk classification if we want safety.
        # However, the 'auto_approve' list usually implies "bypass risk check for these specific actions".
        # Let's keep it that way but respect the execution mode (dry_run_only).
        if any(action in decision_str for action in auto_approve):
            return ActionTier.AUTO_APPROVE if self._can_auto_approve() else ActionTier.ALWAYS_APPROVE

        # 4. Risk tier mapping (risk_tier_match)
        if risk_level == RiskLevel.LOW:
            return ActionTier.AUTO_APPROVE if self._can_auto_approve() else ActionTier.ALWAYS_APPROVE

        # Default for medium/high risk
        return ActionTier.ALWAYS_APPROVE

    def _is_blocked(self, proposal: ActionProposalDTO) -> bool:
        """Check if the proposal's action is in the blocked list."""
        blocked = self.policy.action_tiers.blocked
        return any(action in str(proposal.decision).lower() for action in blocked)

    def _passes_asset_scope(self, proposal: ActionProposalDTO) -> bool:
        """Check if the proposal passes the asset scope filter."""
        if self.policy.asset_scope is not None:
            return self._is_matched_asset(proposal.resource_id)
        return True

    def _can_auto_approve(self) -> bool:
        """Check if auto-approval is allowed by policy."""
        auto_cond = self.policy.auto_approve_conditions
        return not (auto_cond.dry_run_only and self.policy.execution_mode.value != "dry_run")

    def _is_matched_asset(self, resource_id: str) -> bool:
        """Check if a resource matches the configured asset classifier."""
        if self._asset_classifier is None:
            return True
        return self._asset_classifier.classify(resource_id) == "matched"
