from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

from agent_control_plane.engine.policy_engine import PolicyEngine
from agent_control_plane.types.aliases import (
    AliasProfile,
    AliasRegistry,
    FieldAliasMap,
    apply_inbound_aliases,
    apply_outbound_aliases,
)
from agent_control_plane.types.enums import (
    ActionName,
    ActionTier,
    ExecutionMode,
    RiskLevel,
    clear_registered_action_names,
    parse_action_name,
    register_action_names,
)
from agent_control_plane.types.extensions import (
    clear_metadata_schemas,
    clear_risk_limits_extension_schema,
    register_metadata_schema,
    register_risk_limits_extension_schema,
)
from agent_control_plane.types.policies import PolicySnapshotDTO, RiskLimits
from agent_control_plane.types.proposals import ActionProposalDTO
from agent_control_plane.types.sessions import SessionCreate


@pytest.fixture(autouse=True)
def _reset_registries():
    AliasRegistry.clear_profiles()
    clear_registered_action_names()
    clear_metadata_schemas()
    clear_risk_limits_extension_schema()
    yield
    AliasRegistry.clear_profiles()
    clear_registered_action_names()
    clear_metadata_schemas()
    clear_risk_limits_extension_schema()


def test_alias_profile_round_trip_on_action_proposal_and_session():
    profile = AliasProfile(
        name="alphabond",
        aliases=FieldAliasMap(
            canonical_to_alias={
                "resource_id": "security_id",
                "resource_type": "asset_class",
                "weight": "position_size_pct",
                "score": "confidence",
                "max_cost": "max_notional",
                "max_action_count": "max_trade_count",
                "execution_mode": "trading_mode",
            }
        ),
    )
    AliasRegistry.register_profile(profile)

    proposal = ActionProposalDTO.model_validate_with_profile(
        {
            "session_id": str(uuid4()),
            "security_id": "AAPL",
            "asset_class": "equity",
            "decision": "status",
            "reasoning": "check",
            "position_size_pct": "1.5",
            "confidence": "0.8",
        },
        profile="alphabond",
    )
    assert proposal.resource_id == "AAPL"
    dumped = proposal.model_dump_with_profile(profile="alphabond")
    assert dumped["security_id"] == "AAPL"
    assert dumped["position_size_pct"] == Decimal("1.5")

    session = SessionCreate.model_validate_with_profile(
        {
            "session_name": "ab-session",
            "trading_mode": "dry_run",
            "max_notional": "1000",
            "max_trade_count": 20,
        },
        profile="alphabond",
    )
    assert session.max_cost == Decimal("1000")
    assert session.max_action_count == 20


def test_register_custom_action_name_with_fail_closed_unknown():
    register_action_names(["buy"])
    assert parse_action_name("buy") == "buy"
    assert parse_action_name("completely_unknown") == ActionName.UNKNOWN

    policy = PolicySnapshotDTO(
        action_tiers={
            "blocked": ["buy"],
            "always_approve": [],
            "auto_approve": [],
            "unrestricted": [],
        },
        execution_mode=ExecutionMode.DRY_RUN,
    )
    engine = PolicyEngine(policy)
    proposal = ActionProposalDTO(
        session_id=uuid4(),
        resource_id="AAPL",
        resource_type="equity",
        decision="buy",
        reasoning="trade",
        weight=Decimal("1"),
        score=Decimal("0.9"),
    )
    assert engine.classify_action_tier(proposal, RiskLevel.LOW) == ActionTier.BLOCKED


def test_action_proposal_metadata_schema_validation():
    class TradingMetadata(BaseModel):
        target_price: Decimal
        time_horizon: str
        stop_loss: Decimal | None = None
        take_profit: Decimal | None = None

    register_metadata_schema(ActionProposalDTO, TradingMetadata)
    proposal = ActionProposalDTO(
        session_id=uuid4(),
        resource_id="AAPL",
        resource_type="equity",
        decision=ActionName.STATUS,
        reasoning="check",
        metadata={"target_price": "250", "time_horizon": "30d"},
    )
    proposal.validate_metadata()
    typed = proposal.metadata_as()
    assert typed.target_price == Decimal("250")

    bad = ActionProposalDTO(
        session_id=uuid4(),
        resource_id="AAPL",
        resource_type="equity",
        decision=ActionName.STATUS,
        reasoning="check",
        metadata={"time_horizon": "30d"},
    )
    with pytest.raises(ValidationError):
        bad.validate_metadata()


def test_risk_limits_typed_extension_schema():
    class TradingRiskExtension(BaseModel):
        max_duration: Decimal
        max_concentration_pct: Decimal

    register_risk_limits_extension_schema(TradingRiskExtension)
    limits = RiskLimits(custom={"max_duration": Decimal("10"), "max_concentration_pct": Decimal("12.5")})
    limits.validate_extension()
    ext = limits.extension_as()
    assert ext.max_duration == Decimal("10")
    assert ext.max_concentration_pct == Decimal("12.5")

    bad = RiskLimits(custom={"max_duration": Decimal("10")})
    with pytest.raises(ValidationError):
        bad.validate_extension()


def test_risk_limits_validate_extension_raises_without_registered_schema():
    limits = RiskLimits(custom={"foo": Decimal("1")})
    with pytest.raises(ValueError, match="No RiskLimits extension schema registered"):
        limits.validate_extension()


def test_alias_apply_helpers_are_usable_outside_dto_methods():
    profile = AliasProfile(
        name="domain",
        aliases=FieldAliasMap(canonical_to_alias={"resource_id": "security_id", "score": "confidence"}),
    )
    AliasRegistry.register_profile(profile)

    inbound = apply_inbound_aliases({"security_id": "AAPL", "confidence": "0.9"}, "domain")
    assert inbound == {"resource_id": "AAPL", "score": "0.9"}

    outbound = apply_outbound_aliases({"resource_id": "MSFT", "score": "0.7"}, profile)
    assert outbound == {"security_id": "MSFT", "confidence": "0.7"}
