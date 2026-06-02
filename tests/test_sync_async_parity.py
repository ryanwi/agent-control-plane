"""Mechanical parity guard: sync and async facades/protocols must not silently diverge.

This test enforces two contracts:
1. Gateway method-set parity — the sync gateway classes (SessionGateway, ApprovalGateway,
   BudgetGateway, AgenticGateway, ControlPlaneObserver) must stay in sync with
   AsyncControlPlaneFacade's equivalent method set. AsyncControlPlaneFacade refactoring
   to the gateway model is tracked separately; this test will be updated once that split lands.
2. Protocol-pair signature parity — each sync/async repository-protocol pair in
   storage/protocols.py has matching method names and parameter names.

When a protocol pair drifts in parameter names this test fails at CI time — matching
the repo's docs-drift and openapi-check philosophy.
"""

from __future__ import annotations

import inspect

import pytest

from agent_control_plane.async_facade import (
    AsyncAgenticGateway,
    AsyncApprovalGateway,
    AsyncBudgetGateway,
    AsyncControlPlaneObserver,
    AsyncLifecycleGateway,
    AsyncMaintenanceGateway,
    AsyncSessionGateway,
)
from agent_control_plane.storage.protocols import (
    AgentRepository,
    ApprovalRepository,
    AsyncAgentRepository,
    AsyncApprovalRepository,
    AsyncCommandRepository,
    AsyncEventRepository,
    AsyncProposalRepository,
    AsyncSessionRepository,
    AsyncTokenBudgetRepository,
    CommandRepository,
    EventRepository,
    ProposalRepository,
    SessionRepository,
    TokenBudgetRepository,
)
from agent_control_plane.sync import (
    AgenticGateway,
    ApprovalGateway,
    BudgetGateway,
    ControlPlaneObserver,
    SessionGateway,
)

# ── Sync gateway method inventory (for future async-parity check) ────────────

# These are the methods currently on AsyncControlPlaneFacade that correspond to
# the sync gateway classes. Once AsyncControlPlaneFacade is refactored to the same
# gateway model, this inventory becomes the parity check.
ASYNC_ONLY: frozenset[str] = frozenset(
    {
        "acquire_cycle",
        "activate_session",
        "check_stuck_cycles",
        "create_policy",
        "expire_timed_out_tickets",
        "from_session_factory",
        "get_pending_tickets",
        "list_sessions",
        "pause_session",
        "recover_stuck_sessions",
        "release_cycle",
        "resume_session",
        "session_scope",
        "set_active_cycle",
        "token_budget_tracker",  # async-only convenience context manager for token budgets
    }
)

SYNC_ONLY: frozenset[str] = frozenset({"setup"})


def _public_methods(cls: type) -> frozenset[str]:
    return frozenset(name for name in dir(cls) if not name.startswith("_") and callable(getattr(cls, name)))


def test_gateway_method_parity() -> None:
    """Sync and async gateway classes expose the same public methods (modulo allowlists).

    The combined method surface of all sync gateway classes must match the combined
    method surface of all async gateway classes, modulo documented intentional gaps.
    """
    sync_gateway_methods: frozenset[str] = frozenset().union(
        _public_methods(SessionGateway),
        _public_methods(ApprovalGateway),
        _public_methods(BudgetGateway),
        _public_methods(AgenticGateway),
        _public_methods(ControlPlaneObserver),
    )
    async_gateway_methods: frozenset[str] = frozenset().union(
        _public_methods(AsyncSessionGateway),
        _public_methods(AsyncApprovalGateway),
        _public_methods(AsyncBudgetGateway),
        _public_methods(AsyncAgenticGateway),
        _public_methods(AsyncControlPlaneObserver),
        _public_methods(AsyncLifecycleGateway),
        _public_methods(AsyncMaintenanceGateway),
    )

    async_extra = async_gateway_methods - sync_gateway_methods - ASYNC_ONLY
    sync_extra = sync_gateway_methods - async_gateway_methods - SYNC_ONLY

    assert not async_extra, (
        f"Async gateways have methods not covered by any sync gateway "
        f"(add to ASYNC_ONLY or mirror on sync): {sorted(async_extra)}"
    )
    assert not sync_extra, (
        f"Sync gateways have methods not on any async gateway "
        f"(add to SYNC_ONLY or mirror on async): {sorted(sync_extra)}"
    )


# ── Protocol-pair signature parity ───────────────────────────────────────────

PROTOCOL_PAIRS: list[tuple[type, type]] = [
    (SessionRepository, AsyncSessionRepository),
    (EventRepository, AsyncEventRepository),
    (ApprovalRepository, AsyncApprovalRepository),
    (ProposalRepository, AsyncProposalRepository),
    (CommandRepository, AsyncCommandRepository),
    (TokenBudgetRepository, AsyncTokenBudgetRepository),
    (AgentRepository, AsyncAgentRepository),
]


def _protocol_methods(proto: type) -> set[str]:
    return {name for name in dir(proto) if not name.startswith("_") and callable(getattr(proto, name))}


def _param_names(method: object) -> list[str]:
    """Return non-'self' parameter names for a method."""
    try:
        sig = inspect.signature(method)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return []
    return [p for p in sig.parameters if p != "self"]


@pytest.mark.parametrize("sync_proto,async_proto", PROTOCOL_PAIRS)
def test_protocol_pair_method_names(sync_proto: type, async_proto: type) -> None:
    """Sync and async protocol pairs expose the same method names."""
    sync_methods = _protocol_methods(sync_proto)
    async_methods = _protocol_methods(async_proto)
    assert sync_methods == async_methods, (
        f"{sync_proto.__name__} / {async_proto.__name__} method sets diverged.\n"
        f"  sync-only:  {sorted(sync_methods - async_methods)}\n"
        f"  async-only: {sorted(async_methods - sync_methods)}"
    )


@pytest.mark.parametrize("sync_proto,async_proto", PROTOCOL_PAIRS)
def test_protocol_pair_parameter_names(sync_proto: type, async_proto: type) -> None:
    """Shared methods in a sync/async protocol pair have identical parameter names."""
    shared = _protocol_methods(sync_proto) & _protocol_methods(async_proto)
    mismatches: list[str] = []
    for method_name in sorted(shared):
        sync_params = _param_names(getattr(sync_proto, method_name))
        async_params = _param_names(getattr(async_proto, method_name))
        if sync_params != async_params:
            mismatches.append(f"  {method_name}: sync={sync_params} async={async_params}")
    assert not mismatches, f"{sync_proto.__name__} / {async_proto.__name__} parameter drift:\n" + "\n".join(mismatches)
