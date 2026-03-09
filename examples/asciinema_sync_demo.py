"""Asciinema-friendly sync demo for agent-control-plane.

Run:
    uv run python examples/asciinema_sync_demo.py
    uv run python examples/asciinema_sync_demo.py --db ./custom.db
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from agent_control_plane.sync import ControlPlaneFacade, DictEventMapper
from agent_control_plane.types.enums import ApprovalDecisionType, EventKind
from agent_control_plane.types.proposals import ActionProposal

DEFAULT_DB = Path("./control_plane_asciinema_demo.db")


def run_demo(db_path: Path) -> None:
    db_path.unlink(missing_ok=True)

    mapper = DictEventMapper(
        {
            "job_started": EventKind.CYCLE_STARTED,
            "job_completed": EventKind.CYCLE_COMPLETED,
        }
    )
    facade = ControlPlaneFacade.from_database_url(f"sqlite:///{db_path}", mapper=mapper)
    facade.setup()

    session = facade.open_session(
        "asciinema-sync-demo",
        max_cost=Decimal("25.00"),
        max_action_count=5,
        command_id="demo-open-session",
    )
    print(f"session_id={session}")

    proposal = ActionProposal(
        session_id=session,
        resource_id="ticket-42",
        resource_type="support_ticket",
        decision="status",
        reasoning="Demo status check",
        metadata={"source": "asciinema"},
        weight=Decimal("1.25"),
        score=Decimal("0.91"),
    )
    created = facade.create_proposal(proposal, command_id="demo-create-proposal")
    print(f"proposal_id={created.id}")

    ticket = facade.create_ticket(
        session,
        created.id,
        timeout_at=datetime.now(UTC) + timedelta(minutes=10),
        command_id="demo-create-ticket",
    )
    print(f"ticket_id={ticket.id}")

    approved = facade.approve_ticket(
        ticket.id,
        decided_by="ops-demo",
        reason="Approved in terminal demo",
        decision_type=ApprovalDecisionType.ALLOW_ONCE,
        command_id="demo-approve-ticket",
    )
    print(f"ticket_status={approved.status}")

    budget_ok = facade.check_budget(session, cost=Decimal("1.25"), action_count=1)
    print(f"budget_check={budget_ok}")
    facade.increment_budget(session, cost=Decimal("1.25"), action_count=1)

    app_seq = facade.emit_app(
        session,
        "job_started",
        {"job_id": "demo-1", "note": "mapped app event"},
        state_bearing=True,
    )
    print(f"app_event_seq={app_seq}")

    emit_seq = facade.emit(
        session,
        EventKind.EXECUTION_COMPLETED,
        {"proposal_id": str(created.id), "result": "ok"},
        state_bearing=True,
        agent_id="asciinema-agent",
        command_id="demo-emit-execution",
    )
    print(f"execution_event_seq={emit_seq}")

    closed = facade.close_session(
        session,
        payload={"result": "ok"},
        command_id="demo-close-session",
    )
    print(f"final_status={closed.session.status}")

    events = facade.replay(session)
    print(f"event_count={len(events)}")
    facade.close()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Run sync ACP demo for terminal/asciinema recording.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database path for the demo run")
    args = parser.parse_args()

    run_demo(Path(args.db))


if __name__ == "__main__":
    main()
