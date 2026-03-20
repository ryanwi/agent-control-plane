"""Model tier classification and access policy enforcement."""

from __future__ import annotations

import logging

from agent_control_plane.types.enums import ActionTier, ModelTier
from agent_control_plane.types.ids import ModelId
from agent_control_plane.types.token_governance import (
    IdentityContext,
    ModelAccessResult,
    ModelGovernancePolicy,
)

logger = logging.getLogger(__name__)


class ModelAccessDeniedError(Exception):
    """Raised when model access is denied by governance policy."""


class ModelGovernor:
    """Sync engine for model tier classification and access policy."""

    def __init__(self, policy: ModelGovernancePolicy) -> None:
        self._policy = policy

    def classify_model_tier(self, model_id: ModelId) -> ModelTier:
        """Look up the tier for a model, defaulting to STANDARD."""
        return self._policy.model_tier_assignments.get(str(model_id), ModelTier.STANDARD)

    def check_access(
        self,
        model_id: ModelId,
        action_tier: ActionTier,
        identity: IdentityContext | None = None,
    ) -> ModelAccessResult:
        """Check whether a model is allowed for the given action tier and identity."""
        model_tier = self.classify_model_tier(model_id)

        # Identity overrides take precedence
        if identity is not None and identity.user_id is not None:
            user_key = str(identity.user_id)
            if user_key in self._policy.identity_overrides:
                allowed_models = self._policy.identity_overrides[user_key]
                if str(model_id) in allowed_models:
                    return ModelAccessResult(
                        allowed=True,
                        model_id=model_id,
                        model_tier=model_tier,
                    )
                return ModelAccessResult(
                    allowed=False,
                    model_id=model_id,
                    model_tier=model_tier,
                    denial_reason=f"Model {model_id} not in identity override list for user {user_key}",
                )

        # Check tier restrictions
        tier_key = str(action_tier.value) if isinstance(action_tier, ActionTier) else str(action_tier)
        if tier_key in self._policy.tier_restrictions:
            allowed_tiers = self._policy.tier_restrictions[tier_key]
            if str(model_tier.value) not in allowed_tiers:
                return ModelAccessResult(
                    allowed=False,
                    model_id=model_id,
                    model_tier=model_tier,
                    denial_reason=f"Model tier {model_tier.value} not allowed for action tier {tier_key}",
                )

        return ModelAccessResult(
            allowed=True,
            model_id=model_id,
            model_tier=model_tier,
        )

    def get_allowed_models(
        self,
        action_tier: ActionTier,
        identity: IdentityContext | None = None,
    ) -> list[ModelId]:
        """Return all model IDs allowed for the given action tier and identity."""
        allowed: list[ModelId] = []
        for model_id_str in self._policy.model_tier_assignments:
            model_id = ModelId(model_id_str)
            result = self.check_access(model_id, action_tier, identity)
            if result.allowed:
                allowed.append(model_id)
        return allowed
