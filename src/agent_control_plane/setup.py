"""One-stop configuration builder for control plane initialization.

Replaces the multi-step bootstrap ceremony that consumers independently build:
configure aliases → register actions → register risk patterns →
configure model governance → create facade → wrap with resilience.

See ADR-0009 for design rationale.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class GovernanceConfig:
    """Domain-level governance configuration — vocabulary, actions, risk, budgets, policy."""

    alias_profile: AliasProfile | None = None
    action_names: list[str] = field(default_factory=list)
    risk_patterns: list[RiskPattern] | None = None
    model_governance: ModelGovernancePolicy | None = None
    token_budget_configs: list[TokenBudgetConfig] | None = None
    policy: PolicySnapshot | None = None


@dataclass(frozen=True)
class EventConfig:
    """Event mapping configuration — how app events translate to control-plane EventKind values."""

    event_map: dict[str, EventKind] = field(default_factory=dict)
    mapper: AppEventMapper | None = None
    unknown_event_policy: UnknownAppEventPolicy = UnknownAppEventPolicy.IGNORE


@dataclass(frozen=True)
class ResilienceConfig:
    """Resilience configuration — fail-open/fail-closed mode and per-category overrides."""

    mode: ResilienceMode = ResilienceMode.MIXED
    category_overrides: dict[OperationCategory, ResilienceMode] = field(default_factory=dict)


class ControlPlaneSetup:
    """One-stop configuration for control plane initialization.

    Replaces the multi-step bootstrap ceremony both consumers independently built:
    configure_control_plane() → register aliases → register actions →
    register risk patterns → configure model governance → create facade.

    Example::

        cp = ControlPlaneSetup(
            database_url="sqlite:///./cp.db",
            governance=GovernanceConfig(
                alias_profile=MY_ALIASES,
                action_names=["place_order", "cancel_order"],
                risk_patterns=MY_RISK_PATTERNS,
            ),
            events=EventConfig(
                event_map={"order_placed": EventKind.EXECUTION_COMPLETED},
            ),
            resilience=ResilienceConfig(mode=ResilienceMode.MIXED),
        ).build()
    """

    def __init__(
        self,
        database_url: str = "sqlite:///./control_plane.db",
        *,
        governance: GovernanceConfig | None = None,
        events: EventConfig | None = None,
        resilience: ResilienceConfig | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._database_url = database_url
        self._governance = governance or GovernanceConfig()
        self._events = events or EventConfig()
        self._resilience = resilience or ResilienceConfig()
        self._logger = logger

    def build(self) -> ResilientControlPlane:
        """Create tables, register models, configure engines, return ready-to-use CP."""
        self._register_common()
        resolved_mapper = self._resolve_mapper()
        facade = ControlPlaneFacade.from_database_url(
            self._database_url,
            mapper=resolved_mapper,
            unknown_policy=self._events.unknown_event_policy,
        )
        facade.setup()
        return ResilientControlPlane(
            facade,
            mode=self._resilience.mode,
            logger=self._logger,
            category_overrides=self._resilience.category_overrides or None,
        )

    def build_async(self) -> AsyncResilientControlPlane:
        """Async equivalent of build().

        Registers aliases and action names, returns a resilient async wrapper.
        Table creation is handled automatically by AsyncControlPlaneFacade on
        first use (via _ensure_schema).
        """
        from agent_control_plane.async_facade import AsyncControlPlaneFacade
        from agent_control_plane.async_resilient import AsyncResilientControlPlane

        self._register_common()
        resolved_mapper = self._resolve_mapper()
        facade = AsyncControlPlaneFacade.from_database_url(
            self._database_url,
            mapper=resolved_mapper,
            unknown_policy=self._events.unknown_event_policy,
        )
        return AsyncResilientControlPlane(
            facade,
            mode=self._resilience.mode,
            logger=self._logger,
            category_overrides=self._resilience.category_overrides or None,
        )

    def _register_common(self) -> None:
        if self._governance.alias_profile is not None:
            AliasRegistry.register_profile(self._governance.alias_profile)
        if self._governance.action_names:
            register_action_names(self._governance.action_names)

    def _resolve_mapper(self) -> AppEventMapper | None:
        if self._events.mapper is not None:
            return self._events.mapper
        if self._events.event_map:
            return DictEventMapper(self._events.event_map)
        return None

    @property
    def risk_patterns(self) -> list[RiskPattern] | None:
        """Risk patterns for SessionRiskAccumulator (caller creates the engine)."""
        return self._governance.risk_patterns

    @property
    def model_governance(self) -> ModelGovernancePolicy | None:
        """Model governance policy for ModelGovernor (caller creates the engine)."""
        return self._governance.model_governance

    @property
    def token_budget_configs(self) -> list[TokenBudgetConfig] | None:
        """Token budget configs for TokenBudgetTracker.

        After ``build_async()``, use ``cp.token_budget_tracker()`` (async
        context manager) to obtain a session-bound tracker without manual
        ``AsyncSqlAlchemyTokenBudgetRepo(session)`` construction.
        """
        return self._governance.token_budget_configs

    @property
    def policy(self) -> PolicySnapshot | None:
        """Policy snapshot for PolicyEngine (caller creates the engine)."""
        return self._governance.policy
