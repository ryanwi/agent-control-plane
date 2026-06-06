"""ProposalRouter fails closed for a revoked agent (item #4 enforcement)."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from agent_control_plane.engine.agent_registry import AgentRegistry
from agent_control_plane.engine.policy_engine import PolicyEngine
from agent_control_plane.engine.router import ProposalRouter
from agent_control_plane.engine.session_risk_accumulator import SessionRiskAccumulator
from agent_control_plane.types.agents import AgentCapability, AgentMetadata
from agent_control_plane.types.enums import ActionTier
from agent_control_plane.types.policies import ActionTiers, PolicySnapshot
from agent_control_plane.types.proposals import ActionProposal

from .fakes import InMemoryAgentRepository


def _policy() -> PolicySnapshot:
    return PolicySnapshot(action_tiers=ActionTiers(auto_approve=["status"]))


def _proposal(session_id: UUID, **overrides) -> ActionProposal:
    defaults = {
        "session_id": session_id,
        "agent_id": "agent-x",
        "resource_id": "res-1",
        "resource_type": "task",
        "decision": "status",
        "reasoning": "test",
        "weight": Decimal("1.0"),
        "score": Decimal("0.9"),
    }
    defaults.update(overrides)
    return ActionProposal(**defaults)


async def _registry(session_id: UUID, *, revoked: bool) -> AgentRegistry:
    repo = InMemoryAgentRepository()
    registry = AgentRegistry(repo)
    await registry.register(AgentMetadata(id="agent-x", name="X", capabilities=[AgentCapability(action="status")]))
    if revoked:
        await repo.record_revocation(session_id, "agent-x", "suspected compromise")
    return registry


@pytest.mark.asyncio
async def test_route_blocks_revoked_agent():
    sid = uuid4()
    accumulator = SessionRiskAccumulator()
    router = ProposalRouter(
        PolicyEngine(_policy()),
        agent_registry=await _registry(sid, revoked=True),
        risk_accumulator=accumulator,
    )

    decision = await router.route(_proposal(sid))

    assert decision.tier == ActionTier.BLOCKED
    assert "revoked" in decision.reason.lower()
    assert decision.risk_escalated is False
    assert decision.risk_escalation is None
    assert accumulator.get_state(sid) is None


@pytest.mark.asyncio
async def test_route_allows_non_revoked_agent():
    sid = uuid4()
    router = ProposalRouter(PolicyEngine(_policy()), agent_registry=await _registry(sid, revoked=False))

    decision = await router.route(_proposal(sid))

    assert decision.tier == ActionTier.AUTO_APPROVE
