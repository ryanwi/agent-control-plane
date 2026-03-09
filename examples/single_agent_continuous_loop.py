"""Single-agent continuous governance loop demo.

Run:
    uv run python examples/single_agent_continuous_loop.py
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from time import sleep

from agent_control_plane.sync import ControlPlaneFacade, DictEventMapper
from agent_control_plane.types.enums import ApprovalDecisionType, EventKind
from agent_control_plane.types.proposals import ActionProposal


def main() -> None:
    db_path = Path("./single_agent_continuous_demo.db")
    db_path.unlink(missing_ok=True)

    cp = ControlPlaneFacade.from_database_url(
        f"sqlite:///{db_path}",
        mapper=DictEventMapper({"loop_started": EventKind.CYCLE_STARTED, "loop_finished": EventKind.CYCLE_COMPLETED}),
    )
    cp.setup()

    session_id = cp.open_session(
        "single-agent-continuous-demo",
        max_cost=Decimal("10.00"),
        max_action_count=4,
        command_id="single-open-session",
    )

    cycle_plan = [("case-6001", False), ("case-6002", True)]
    for cycle_no, (case_id, should_approve) in enumerate(cycle_plan, start=1):
        proposal = ActionProposal(
            session_id=session_id,
            resource_id=case_id,
            resource_type="customer_case",
            decision="status",
            reasoning=f"single-agent cycle {cycle_no}",
            metadata={"agent": "single-agent", "cycle": cycle_no},
            weight=Decimal("0.60"),
            score=Decimal("0.75"),
        )
        proposal = cp.create_proposal(proposal, command_id=f"single-cycle-{cycle_no}-proposal")

        ticket = cp.create_ticket(
            session_id,
            proposal.id,
            timeout_at=datetime.now(UTC) + timedelta(minutes=5),
            command_id=f"single-cycle-{cycle_no}-ticket",
        )

        if should_approve:
            cp.approve_ticket(
                ticket.id,
                decided_by="single-agent",
                reason="Cycle configured to approve",
                decision_type=ApprovalDecisionType.ALLOW_ONCE,
                command_id=f"single-cycle-{cycle_no}-approve",
            )
            cp.emit(
                session_id,
                EventKind.APPROVAL_GRANTED,
                {"case_id": case_id, "cycle": cycle_no},
                state_bearing=True,
                command_id=f"single-cycle-{cycle_no}-emit-granted",
            )
            cp.emit(
                session_id,
                EventKind.EXECUTION_COMPLETED,
                {"case_id": case_id, "cycle": cycle_no, "result": "status sent"},
                state_bearing=True,
                agent_id="single-agent",
                command_id=f"single-cycle-{cycle_no}-emit-executed",
            )
            print(f"cycle={cycle_no} case={case_id} approved")
        else:
            cp.deny_ticket(
                ticket.id,
                reason="Cycle configured to deny",
                command_id=f"single-cycle-{cycle_no}-deny",
            )
            cp.emit(
                session_id,
                EventKind.APPROVAL_DENIED,
                {"case_id": case_id, "cycle": cycle_no},
                state_bearing=True,
                agent_id="single-agent",
                command_id=f"single-cycle-{cycle_no}-emit-denied",
            )
            print(f"cycle={cycle_no} case={case_id} denied")

        sleep(0.2)

    cp.close_session(
        session_id,
        payload={"summary": "single-agent continuous demo completed"},
        command_id="single-close-session",
    )

    print("events_recorded=", len(cp.replay(session_id)))
    print("db_path=", db_path)


if __name__ == "__main__":
    main()
