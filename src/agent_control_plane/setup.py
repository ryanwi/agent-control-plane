"""One-stop configuration builder for control plane initialization.

Replaces the multi-step bootstrap ceremony that consumers independently build:
configure aliases → register actions → register risk patterns →
configure model governance → create facade → wrap with resilience.

See ADR-0009 for design rationale.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_control_plane.async_resilient import AsyncResilientControlPlane

from agent_control_plane.resilient import ResilientControlPlane
from agent_control_plane.sync import (
    AppEventMapper,
    ControlPlaneFacade,
    DictEventMapper,
)
from agent_control_plane.types.aliases import AliasProfile, AliasRegistry
from agent_control_plane.types.enums import (
    EventKind,
    OperationCategory,
    ResilienceMode,
    UnknownAppEventPolicy,
    register_action_names,
)
from agent_control_plane.types.policies import PolicySnapshot
from agent_control_plane.types.risk import RiskPattern
from agent_control_plane.types.token_governance import (
    ModelGovernancePolicy,
    TokenBudgetConfig,
)


class ControlPlaneSetup:
    """One-stop configuration for control plane initialization.

    Replaces the multi-step bootstrap ceremony both consumers independently built:
    configure_control_plane() → register aliases → register actions →
    register risk patterns → configure model governance → create facade.

    Example::

        cp = ControlPlaneSetup(
            database_url="sqlite:///./cp.db",
            alias_profile=MY_ALIASES,
            action_names=["place_order", "cancel_order"],
            event_map={"order_placed": EventKind.EXECUTION_COMPLETED},
            risk_patterns=MY_RISK_PATTERNS,
            resilience_mode=ResilienceMode.MIXED,
        ).build()
    """

    def __init__(
        self,
        database_url: str = "sqlite:///./control_plane.db",
        *,
        # Domain vocabulary
        alias_profile: AliasProfile | None = None,
        # Action classification
        action_names: list[str] | None = None,
        # Event mapping
        event_map: dict[str, EventKind] | None = None,
        mapper: AppEventMapper | None = None,
        unknown_event_policy: UnknownAppEventPolicy = UnknownAppEventPolicy.IGNORE,
        # Risk patterns (for SessionRiskAccumulator)
        risk_patterns: list[RiskPattern] | None = None,
        # Model governance (for ModelGovernor)
        model_governance: ModelGovernancePolicy | None = None,
        # Token budgets (for TokenBudgetTracker)
        token_budget_configs: list[TokenBudgetConfig] | None = None,
        # Policy snapshot
        policy: PolicySnapshot | None = None,
        # Resilience
        resilience_mode: ResilienceMode = ResilienceMode.MIXED,
        category_overrides: dict[OperationCategory, ResilienceMode] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._database_url = database_url
        self._alias_profile = alias_profile
        self._action_names = action_names
        self._event_map = event_map
        self._mapper = mapper
        self._unknown_event_policy = unknown_event_policy
        self._risk_patterns = risk_patterns
        self._model_governance = model_governance
        self._token_budget_configs = token_budget_configs
        self._policy = policy
        self._resilience_mode = resilience_mode
        self._category_overrides = category_overrides
        self._logger = logger

    def build(self) -> ResilientControlPlane:
        """Create tables, register models, configure engines, return ready-to-use CP."""
        # Register alias profile
        if self._alias_profile is not None:
            AliasRegistry.register_profile(self._alias_profile)

        # Register action names
        if self._action_names:
            register_action_names(self._action_names)

        # Build event mapper
        resolved_mapper = self._mapper
        if resolved_mapper is None and self._event_map is not None:
            resolved_mapper = DictEventMapper(self._event_map)

        # Create facade
        facade = ControlPlaneFacade.from_database_url(
            self._database_url,
            mapper=resolved_mapper,
            unknown_policy=self._unknown_event_policy,
        )
        facade.setup()

        # Wrap with resilience
        return ResilientControlPlane(
            facade,
            mode=self._resilience_mode,
            logger=self._logger,
            category_overrides=self._category_overrides,
        )

    def build_async(self) -> AsyncResilientControlPlane:
        """Async equivalent of build().

        Registers aliases and action names, returns a resilient async wrapper.
        Table creation is handled automatically by AsyncControlPlaneFacade on
        first use (via _ensure_schema).
        """
        from agent_control_plane.async_facade import AsyncControlPlaneFacade
        from agent_control_plane.async_resilient import AsyncResilientControlPlane

        # Register alias profile
        if self._alias_profile is not None:
            AliasRegistry.register_profile(self._alias_profile)

        # Register action names
        if self._action_names:
            register_action_names(self._action_names)

        # Build event mapper
        resolved_mapper = self._mapper
        if resolved_mapper is None and self._event_map is not None:
            resolved_mapper = DictEventMapper(self._event_map)

        # Create async facade
        facade = AsyncControlPlaneFacade.from_database_url(
            self._database_url,
            mapper=resolved_mapper,
            unknown_policy=self._unknown_event_policy,
        )

        # Wrap with resilience
        return AsyncResilientControlPlane(
            facade,
            mode=self._resilience_mode,
            logger=self._logger,
            category_overrides=self._category_overrides,
        )

    @property
    def risk_patterns(self) -> list[RiskPattern] | None:
        """Risk patterns for SessionRiskAccumulator (caller creates the engine)."""
        return self._risk_patterns

    @property
    def model_governance(self) -> ModelGovernancePolicy | None:
        """Model governance policy for ModelGovernor (caller creates the engine)."""
        return self._model_governance

    @property
    def token_budget_configs(self) -> list[TokenBudgetConfig] | None:
        """Token budget configs for TokenBudgetTracker (caller creates the engine)."""
        return self._token_budget_configs

    @property
    def policy(self) -> PolicySnapshot | None:
        """Policy snapshot for PolicyEngine (caller creates the engine)."""
        return self._policy
