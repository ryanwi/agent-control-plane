"""Tests for experimental capability contracts and composition wiring."""

from __future__ import annotations

from decimal import Decimal

import pytest

from agent_control_plane.builders import build_session_event_budget
from agent_control_plane.experimental.capabilities import (
    ControlPlaneCapability,
    StaticCapabilityProvider,
    capability_set_from_mapping,
)
from agent_control_plane.types.enums import ExecutionMode, SessionStatus
from tests.fakes import InMemoryEventRepository, InMemorySessionRepository


def test_capability_set_from_mapping_builds_descriptors() -> None:
    capabilities = capability_set_from_mapping(
        {
            ControlPlaneCapability.FLEET_MANAGEMENT.value: {"version": "2026.03", "scope": "global"},
            "custom_ops": {"tier": "pro"},
        }
    )
    assert capabilities.has(ControlPlaneCapability.FLEET_MANAGEMENT)
    assert capabilities.has("custom_ops")
    fleet = next(item for item in capabilities.items if item.name == ControlPlaneCapability.FLEET_MANAGEMENT.value)
    assert fleet.version == "2026.03"
    assert fleet.metadata["scope"] == "global"


@pytest.mark.asyncio
async def test_builder_capability_detection_does_not_change_budget_outcome() -> None:
    session_repo_plain = InMemorySessionRepository()
    session_repo_with_caps = InMemorySessionRepository()
    event_repo_plain = InMemoryEventRepository()
    event_repo_with_caps = InMemoryEventRepository()

    plain_session = await session_repo_plain.create_session(
        session_name="plain",
        status=SessionStatus.CREATED,
        execution_mode=ExecutionMode.DRY_RUN,
        max_cost=Decimal("10"),
        max_action_count=5,
    )
    cap_session = await session_repo_with_caps.create_session(
        session_name="with-caps",
        status=SessionStatus.CREATED,
        execution_mode=ExecutionMode.DRY_RUN,
        max_cost=Decimal("10"),
        max_action_count=5,
    )

    plain = build_session_event_budget(session_repo=session_repo_plain, event_repo=event_repo_plain)
    provider = StaticCapabilityProvider(
        capability_set_from_mapping({ControlPlaneCapability.MANAGED_OPERATIONS.value: {"version": "exp-1"}})
    )
    with_caps = build_session_event_budget(
        session_repo=session_repo_with_caps,
        event_repo=event_repo_with_caps,
        capability_provider=provider,
    )

    assert plain.get_capabilities().items == []
    assert with_caps.get_capabilities().has(ControlPlaneCapability.MANAGED_OPERATIONS)

    plain_allowed = await plain.budget_tracker.check_budget(plain_session.id, cost=Decimal("3"), action_count=1)
    caps_allowed = await with_caps.budget_tracker.check_budget(cap_session.id, cost=Decimal("3"), action_count=1)
    assert plain_allowed == caps_allowed
