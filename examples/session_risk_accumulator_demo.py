"""SessionRiskAccumulator demo.

Demonstrates:
1. Score-based escalation: LOW actions accumulate until threshold is crossed.
2. Pattern-based escalation: detecting a data-exfiltration action chain.
3. Session isolation: two sessions accrue risk independently.
4. Event emission: SESSION_RISK_ESCALATED telemetry when escalation occurs.

Run:
    uv run python examples/session_risk_accumulator_demo.py
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import uuid4

from agent_control_plane.engine.event_store import EventStore
from agent_control_plane.engine.session_risk_accumulator import SessionRiskAccumulator
from agent_control_plane.types.enums import EventKind, RiskLevel, register_action_names
from agent_control_plane.types.proposals import ActionProposal
from agent_control_plane.types.risk import RiskPattern

# Register domain-specific action names so they pass through parse_action_name
register_action_names(["read_crm", "query_database", "send_email", "list_customers"])


def _proposal(session_id, decision: str) -> ActionProposal:
    return ActionProposal(
        session_id=session_id,
        resource_id="demo-resource",
        resource_type="task",
        decision=decision,
        reasoning="demo",
    )


async def demo_score_accumulation() -> None:
    print("\n=== 1. Score-based escalation ===")
    acc = SessionRiskAccumulator(
        score_threshold_medium=Decimal("5.0"),
        score_threshold_high=Decimal("10.0"),
    )
    sid = uuid4()

    for i in range(1, 8):
        result = await acc.assess(sid, _proposal(sid, "list_customers"), RiskLevel.LOW)
        state = result.session_state
        print(
            f"  action {i}: score={state.accumulated_score:.1f}  "
            f"risk={result.escalated_risk.value}  escalated={result.was_escalated}"
        )
        if result.escalation_reasons:
            for r in result.escalation_reasons:
                print(f"    reason: {r}")


async def demo_pattern_detection() -> None:
    print("\n=== 2. Pattern-based escalation (data exfiltration chain) ===")
    exfil_pattern = RiskPattern(
        name="data_exfiltration",
        description="CRM read → DB query → email = likely data exfiltration",
        action_sequence=["read_crm", "query_database", "send_email"],
        window_size=10,
        escalate_to=RiskLevel.HIGH,
    )
    acc = SessionRiskAccumulator(patterns=[exfil_pattern])
    sid = uuid4()

    steps = [
        ("read_crm", RiskLevel.LOW),
        ("query_database", RiskLevel.MEDIUM),
        ("send_email", RiskLevel.LOW),
    ]
    for decision, risk in steps:
        result = await acc.assess(sid, _proposal(sid, decision), risk)
        print(f"  {decision}: escalated_risk={result.escalated_risk.value}  " f"was_escalated={result.was_escalated}")
        for r in result.escalation_reasons:
            print(f"    reason: {r}")


async def demo_session_isolation() -> None:
    print("\n=== 3. Session isolation ===")
    acc = SessionRiskAccumulator()
    sid_a, sid_b = uuid4(), uuid4()

    # Session A: 6 LOW actions → score 6.0 → MEDIUM escalation
    for _ in range(6):
        await acc.assess(sid_a, _proposal(sid_a, "list_customers"), RiskLevel.LOW)

    # Session B: 1 LOW action → score 1.0, no escalation
    await acc.assess(sid_b, _proposal(sid_b, "list_customers"), RiskLevel.LOW)

    state_a = acc.get_state(sid_a)
    state_b = acc.get_state(sid_b)
    print(f"  session_a: score={state_a.accumulated_score:.1f}  risk={state_a.current_risk_level.value}")
    print(f"  session_b: score={state_b.accumulated_score:.1f}  risk={state_b.current_risk_level.value}")


async def demo_event_emission() -> None:
    print("\n=== 4. Event emission ===")
    # Use an in-memory SQLite event repo for illustration
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from agent_control_plane.models.reference import Base, register_models
    from agent_control_plane.storage.sqlalchemy_async import AsyncSqlAlchemyUnitOfWork

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    register_models()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    uow = AsyncSqlAlchemyUnitOfWork(session_factory)

    async with uow:
        event_store = EventStore(uow.events)
        acc = SessionRiskAccumulator(
            score_threshold_medium=Decimal("5.0"),
            event_store=event_store,
        )
        sid = uuid4()

        # Drive score above medium threshold
        for _ in range(5):
            await acc.assess(sid, _proposal(sid, "list_customers"), RiskLevel.LOW)

        await uow.commit()
        events = await uow.events.replay(sid)
        escalation_events = [e for e in events if e.event_kind == EventKind.SESSION_RISK_ESCALATED]
        print(f"  SESSION_RISK_ESCALATED events emitted: {len(escalation_events)}")
        for e in escalation_events:
            print(f"    payload: {e.payload}")

    await engine.dispose()


async def main() -> None:
    print("SessionRiskAccumulator demo — agent-control-plane v0.9.6")
    await demo_score_accumulation()
    await demo_pattern_detection()
    await demo_session_isolation()
    await demo_event_emission()
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
