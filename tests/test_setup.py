"""Tests for ControlPlaneSetup configuration builder."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from agent_control_plane.resilient import ResilientControlPlane
from agent_control_plane.setup import ControlPlaneSetup
from agent_control_plane.types.enums import (
    EventKind,
    OperationCategory,
    ResilienceMode,
    clear_registered_action_names,
    is_registered_action_name,
)
from agent_control_plane.types.risk import RiskPattern
from agent_control_plane.types.token_governance import ModelGovernancePolicy


@pytest.fixture()
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path}/setup_test.db"


@pytest.fixture(autouse=True)
def _cleanup_action_names():
    yield
    clear_registered_action_names()


class TestBasicBuild:
    """Build a working CP with minimal config."""

    def test_build_returns_resilient_cp(self, db_url: str) -> None:
        cp = ControlPlaneSetup(db_url).build()
        assert isinstance(cp, ResilientControlPlane)
        cp.close()

    def test_can_open_and_close_session(self, db_url: str) -> None:
        cp = ControlPlaneSetup(db_url).build()
        sid = cp.open_session("test", max_cost=Decimal("100"))
        session = cp.get_session(sid)
        assert session is not None
        cp.close_session(sid)
        cp.close()

    def test_default_resilience_is_mixed(self, db_url: str) -> None:
        cp = ControlPlaneSetup(db_url).build()
        session_id = cp.open_session("test")
        result = cp.check_budget(session_id, cost=Decimal("10"))
        assert result is True
        cp.close()


class TestWithEventMap:
    """Build with event mapping configured."""

    def test_emit_app_works(self, db_url: str) -> None:
        cp = ControlPlaneSetup(
            db_url,
            event_map={"job_started": EventKind.CYCLE_STARTED},
        ).build()
        sid = cp.open_session("test")
        seq = cp.emit_app(sid, "job_started", {"job_id": "1"})
        assert isinstance(seq, int)
        cp.close()


class TestWithActionNames:
    """Build with custom action names registered."""

    def test_action_names_registered(self, db_url: str) -> None:
        cp = ControlPlaneSetup(
            db_url,
            action_names=["place_order", "cancel_order"],
        ).build()
        assert is_registered_action_name("place_order")
        assert is_registered_action_name("cancel_order")
        cp.close()


class TestWithResilienceMode:
    """Build with explicit resilience mode."""

    def test_fail_closed_mode(self, db_url: str) -> None:
        cp = ControlPlaneSetup(
            db_url,
            resilience_mode=ResilienceMode.FAIL_CLOSED,
        ).build()
        with pytest.raises((ValueError, OSError)):
            cp.check_budget(__import__("uuid").UUID(int=999))
        cp.close()

    def test_fail_open_mode(self, db_url: str) -> None:
        cp = ControlPlaneSetup(
            db_url,
            resilience_mode=ResilienceMode.FAIL_OPEN,
        ).build()
        result = cp.check_budget(__import__("uuid").UUID(int=999))
        assert result is True
        cp.close()


class TestWithCategoryOverrides:
    """Build with per-category resilience overrides."""

    def test_category_override_applied(self, db_url: str) -> None:
        cp = ControlPlaneSetup(
            db_url,
            category_overrides={OperationCategory.BUDGET: ResilienceMode.FAIL_CLOSED},
        ).build()
        with pytest.raises((ValueError, OSError)):
            cp.check_budget(__import__("uuid").UUID(int=999))
        cp.close()


class TestPropertyAccessors:
    """Config properties are accessible for composable engine setup."""

    def test_risk_patterns_accessible(self, db_url: str) -> None:
        patterns = [
            RiskPattern(
                name="test",
                description="test pattern",
                action_sequence=["execute_trade"],
                escalate_to="high",
            )
        ]
        setup = ControlPlaneSetup(db_url, risk_patterns=patterns)
        assert setup.risk_patterns == patterns

    def test_model_governance_accessible(self, db_url: str) -> None:
        policy = ModelGovernancePolicy(default_model_tier="standard")
        setup = ControlPlaneSetup(db_url, model_governance=policy)
        assert setup.model_governance is policy

    def test_token_budget_configs_accessible(self, db_url: str) -> None:
        setup = ControlPlaneSetup(db_url, token_budget_configs=[])
        assert setup.token_budget_configs == []

    def test_none_by_default(self, db_url: str) -> None:
        setup = ControlPlaneSetup(db_url)
        assert setup.risk_patterns is None
        assert setup.model_governance is None
        assert setup.token_budget_configs is None
        assert setup.policy is None
