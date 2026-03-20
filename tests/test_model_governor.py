"""Tests for ModelGovernor engine."""

import pytest

from agent_control_plane.engine.model_governor import ModelGovernor
from agent_control_plane.types.enums import ActionTier, ModelTier
from agent_control_plane.types.ids import ModelId, UserId
from agent_control_plane.types.token_governance import IdentityContext, ModelGovernancePolicy


@pytest.fixture
def policy() -> ModelGovernancePolicy:
    return ModelGovernancePolicy(
        model_tier_assignments={
            "gpt-4": ModelTier.PREMIUM,
            "gpt-3.5": ModelTier.STANDARD,
            "claude-opus": ModelTier.RESTRICTED,
        },
        tier_restrictions={
            "auto_approve": ["standard"],
            "always_approve": ["standard", "premium"],
            "unrestricted": ["standard", "premium", "restricted"],
        },
        identity_overrides={
            "admin-user": ["gpt-4", "claude-opus", "gpt-3.5"],
        },
    )


@pytest.fixture
def governor(policy: ModelGovernancePolicy) -> ModelGovernor:
    return ModelGovernor(policy)


class TestClassifyModelTier:
    def test_known_model(self, governor: ModelGovernor) -> None:
        assert governor.classify_model_tier(ModelId("gpt-4")) == ModelTier.PREMIUM

    def test_unknown_model_defaults_standard(self, governor: ModelGovernor) -> None:
        assert governor.classify_model_tier(ModelId("unknown-model")) == ModelTier.STANDARD

    def test_restricted_model(self, governor: ModelGovernor) -> None:
        assert governor.classify_model_tier(ModelId("claude-opus")) == ModelTier.RESTRICTED


class TestCheckAccess:
    def test_standard_model_auto_approve(self, governor: ModelGovernor) -> None:
        result = governor.check_access(ModelId("gpt-3.5"), ActionTier.AUTO_APPROVE)
        assert result.allowed is True
        assert result.model_tier == ModelTier.STANDARD

    def test_premium_model_denied_for_auto_approve(self, governor: ModelGovernor) -> None:
        result = governor.check_access(ModelId("gpt-4"), ActionTier.AUTO_APPROVE)
        assert result.allowed is False
        assert "not allowed" in (result.denial_reason or "")

    def test_premium_model_allowed_for_always_approve(self, governor: ModelGovernor) -> None:
        result = governor.check_access(ModelId("gpt-4"), ActionTier.ALWAYS_APPROVE)
        assert result.allowed is True

    def test_restricted_model_only_unrestricted_tier(self, governor: ModelGovernor) -> None:
        result = governor.check_access(ModelId("claude-opus"), ActionTier.ALWAYS_APPROVE)
        assert result.allowed is False

        result = governor.check_access(ModelId("claude-opus"), ActionTier.UNRESTRICTED)
        assert result.allowed is True

    def test_identity_override_grants_access(self, governor: ModelGovernor) -> None:
        identity = IdentityContext(user_id=UserId("admin-user"))
        result = governor.check_access(ModelId("claude-opus"), ActionTier.AUTO_APPROVE, identity)
        assert result.allowed is True

    def test_identity_override_denies_unlisted_model(self, governor: ModelGovernor) -> None:
        identity = IdentityContext(user_id=UserId("admin-user"))
        result = governor.check_access(ModelId("some-other-model"), ActionTier.AUTO_APPROVE, identity)
        assert result.allowed is False
        assert "not in identity override" in (result.denial_reason or "")

    def test_no_tier_restriction_allows(self, governor: ModelGovernor) -> None:
        # BLOCKED tier has no entry in tier_restrictions, so no restriction applies
        result = governor.check_access(ModelId("gpt-3.5"), ActionTier.BLOCKED)
        assert result.allowed is True

    def test_unknown_model_standard_tier(self, governor: ModelGovernor) -> None:
        result = governor.check_access(ModelId("new-model"), ActionTier.AUTO_APPROVE)
        assert result.allowed is True
        assert result.model_tier == ModelTier.STANDARD


class TestGetAllowedModels:
    def test_auto_approve_only_standard(self, governor: ModelGovernor) -> None:
        allowed = governor.get_allowed_models(ActionTier.AUTO_APPROVE)
        assert ModelId("gpt-3.5") in allowed
        assert ModelId("gpt-4") not in allowed
        assert ModelId("claude-opus") not in allowed

    def test_unrestricted_allows_all(self, governor: ModelGovernor) -> None:
        allowed = governor.get_allowed_models(ActionTier.UNRESTRICTED)
        assert len(allowed) == 3

    def test_identity_override_in_allowed(self, governor: ModelGovernor) -> None:
        identity = IdentityContext(user_id=UserId("admin-user"))
        allowed = governor.get_allowed_models(ActionTier.AUTO_APPROVE, identity)
        assert ModelId("claude-opus") in allowed
        assert ModelId("gpt-4") in allowed


class TestEmptyPolicy:
    def test_empty_policy_allows_all(self) -> None:
        governor = ModelGovernor(ModelGovernancePolicy())
        result = governor.check_access(ModelId("anything"), ActionTier.AUTO_APPROVE)
        assert result.allowed is True
        assert result.model_tier == ModelTier.STANDARD
