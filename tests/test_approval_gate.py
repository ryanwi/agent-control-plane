import pytest
from decimal import Decimal
from uuid import uuid4

from agent_control_plane.engine.approval_gate import ApprovalGate
from agent_control_plane.engine.event_store import EventStore
from agent_control_plane.models.registry import ModelRegistry
from agent_control_plane.types.enums import ApprovalDecisionType, ApprovalStatus, RiskLevel


class _FakeTicket:
    def __init__(
        self,
        *,
        session_id,
        status,
        decision_type,
        scope_symbols=None,
        scope_max_notional=None,
        scope_max_count=None,
        scope_expiry=None,
    ):
        self.session_id = session_id
        self.status = status
        self.decision_type = decision_type
        self.scope_symbols = scope_symbols
        self.scope_max_notional = scope_max_notional
        self.scope_max_count = scope_max_count
        self.scope_expiry = scope_expiry


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeSession:
    def __init__(self, rows):
        self.rows = rows
        self.flushed = False

    async def execute(self, *_args, **_kwargs):
        return _FakeResult(self.rows)

    async def flush(self):
        self.flushed = True


class _TicketModel:
    pass


@pytest.fixture(autouse=True)
def approval_ticket_model_registry():
    ModelRegistry.register("ApprovalTicket", _TicketModel)
    yield
    ModelRegistry.reset()


@pytest.mark.asyncio
async def test_check_session_scope_consumes_scope_count():
    session_id = uuid4()
    ticket = _FakeTicket(
        session_id=session_id,
        status=ApprovalStatus.APPROVED,
        decision_type=ApprovalDecisionType.ALLOW_FOR_SESSION,
        scope_symbols=["AAPL"],
        scope_max_notional=Decimal("100"),
        scope_max_count=2,
    )

    session = _FakeSession([ticket])
    gate = ApprovalGate(EventStore())

    result = await gate.check_session_scope(
        session,
        session_id=session_id,
        security_id="AAPL",
        risk_level=RiskLevel.MEDIUM,
        notional=Decimal("10"),
    )

    assert result is ticket
    assert ticket.scope_max_count == 1
    assert session.flushed


@pytest.mark.asyncio
async def test_check_session_scope_blocks_when_count_is_exhausted():
    session_id = uuid4()
    ticket = _FakeTicket(
        session_id=session_id,
        status=ApprovalStatus.APPROVED,
        decision_type=ApprovalDecisionType.ALLOW_FOR_SESSION,
        scope_symbols=["AAPL"],
        scope_max_notional=Decimal("100"),
        scope_max_count=0,
    )

    session = _FakeSession([ticket])
    gate = ApprovalGate(EventStore())

    result = await gate.check_session_scope(
        session,
        session_id=session_id,
        security_id="AAPL",
        risk_level=RiskLevel.MEDIUM,
        notional=Decimal("10"),
    )

    assert result is None
    assert ticket.scope_max_count == 0
    assert session.flushed is False
