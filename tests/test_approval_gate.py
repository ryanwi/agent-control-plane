"""Tests for ApprovalGate session-scope checking."""

from decimal import Decimal
from uuid import uuid4

import pytest

from agent_control_plane.engine.approval_gate import ApprovalGate
from agent_control_plane.engine.event_store import EventStore
from agent_control_plane.types.enums import ApprovalDecisionType, ApprovalStatus

from .fakes import InMemoryApprovalRepository, InMemoryEventRepository, InMemoryProposalRepository


def _make_gate():
    event_repo = InMemoryEventRepository()
    approval_repo = InMemoryApprovalRepository()
    proposal_repo = InMemoryProposalRepository()
    es = EventStore(event_repo)
    gate = ApprovalGate(es, approval_repo, proposal_repo)
    return gate, approval_repo


@pytest.mark.asyncio
async def test_check_session_scope_consumes_scope_count():
    gate, approval_repo = _make_gate()
    session_id = uuid4()
    proposal_id = uuid4()

    from datetime import UTC, datetime, timedelta

    ticket = await approval_repo.create_ticket(session_id, proposal_id, datetime.now(UTC) + timedelta(hours=1))
    # Manually approve with scope
    await approval_repo.update_ticket(
        ticket.id,
        status=ApprovalStatus.APPROVED,
        decision_type=ApprovalDecisionType.ALLOW_FOR_SESSION,
        scope_resource_ids=["res-001"],
        scope_max_cost=Decimal("100"),
        scope_max_count=2,
    )

    result = await gate.check_session_scope(
        session_id=session_id,
        resource_id="res-001",
        cost=Decimal("10"),
    )

    assert result is not None
    assert result.scope_max_count == 1


@pytest.mark.asyncio
async def test_check_session_scope_blocks_when_count_is_exhausted():
    gate, approval_repo = _make_gate()
    session_id = uuid4()
    proposal_id = uuid4()

    from datetime import UTC, datetime, timedelta

    ticket = await approval_repo.create_ticket(session_id, proposal_id, datetime.now(UTC) + timedelta(hours=1))
    await approval_repo.update_ticket(
        ticket.id,
        status=ApprovalStatus.APPROVED,
        decision_type=ApprovalDecisionType.ALLOW_FOR_SESSION,
        scope_resource_ids=["res-001"],
        scope_max_cost=Decimal("100"),
        scope_max_count=0,
    )

    result = await gate.check_session_scope(
        session_id=session_id,
        resource_id="res-001",
        cost=Decimal("10"),
    )

    assert result is None
