"""Multi-agent continuous governance loop demo.

Run:
    uv run python examples/multi_agent_continuous_loop.py
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from time import sleep

from agent_control_plane.sync import ControlPlaneFacade, DictEventMapper
from agent_control_plane.types.enums import ApprovalDecisionType, EventKind
from agent_control_plane.types.proposals import ActionProposal


class PlannerAgent:
    def propose(self, *, session_id, case_id: str, cycle_no: int) -> ActionProposal:
        return ActionProposal(
            session_id=session_id,
            resource_id=case_id,
            resource_type="customer_case",
            decision="status",
            reasoning=f"planner proposal for cycle {cycle_no}",
            metadata={"producer": "planner-agent", "cycle": cycle_no},
            weight=Decimal("0.60"),
            score=Decimal("0.75"),
        )


class ReviewerAgent:
    def review(self, *, case_id: str, cycle_no: int) -> bool:
        # Deterministic demo behavior: first case denied, second approved.
        return cycle_no % 2 == 0 and case_id.endswith("2")


def main() -> None:
    db_path = Path("./multi_agent_continuous_demo.db")
    db_path.unlink(missing_ok=True)

    cp = ControlPlaneFacade.from_database_url(
        f"sqlite:///{db_path}",
        mapper=DictEventMapper({"loop_started": EventKind.CYCLE_STARTED, "loop_finished": EventKind.CYCLE_COMPLETED}),
    )
    cp.setup()

    session_id = cp.open_session(
        "multi-agent-continuous-demo",
        max_cost=Decimal("10.00"),
        max_action_count=4,
        command_id="multi-open-session",
    )

    planner = PlannerAgent()
    reviewer = ReviewerAgent()
    cycle_plan = ["case-7001", "case-7002"]

    for cycle_no, case_id in enumerate(cycle_plan, start=1):
        proposal = planner.propose(session_id=session_id, case_id=case_id, cycle_no=cycle_no)
        proposal = cp.create_proposal(proposal, command_id=f"multi-cycle-{cycle_no}-proposal")

        ticket = cp.create_ticket(
            session_id,
            proposal.id,
            timeout_at=datetime.now(UTC) + timedelta(minutes=5),
            command_id=f"multi-cycle-{cycle_no}-ticket",
        )

        approved = reviewer.review(case_id=case_id, cycle_no=cycle_no)
        if approved:
            cp.approve_ticket(
                ticket.id,
                decided_by="reviewer-agent",
                reason="Reviewer approved",
                decision_type=ApprovalDecisionType.ALLOW_ONCE,
                command_id=f"multi-cycle-{cycle_no}-approve",
            )
            cp.emit(
                session_id,
                EventKind.APPROVAL_GRANTED,
                {"case_id": case_id, "cycle": cycle_no, "reviewer": "reviewer-agent"},
                state_bearing=True,
                command_id=f"multi-cycle-{cycle_no}-emit-granted",
            )
            cp.emit(
                session_id,
                EventKind.EXECUTION_COMPLETED,
                {"case_id": case_id, "cycle": cycle_no, "result": "status sent"},
                state_bearing=True,
                agent_id="planner-agent",
                command_id=f"multi-cycle-{cycle_no}-emit-executed",
            )
            print(f"cycle={cycle_no} case={case_id} approved")
        else:
            cp.deny_ticket(
                ticket.id,
                reason="Reviewer denied",
                command_id=f"multi-cycle-{cycle_no}-deny",
            )
            cp.emit(
                session_id,
                EventKind.APPROVAL_DENIED,
                {"case_id": case_id, "cycle": cycle_no, "reviewer": "reviewer-agent"},
                state_bearing=True,
                agent_id="reviewer-agent",
                command_id=f"multi-cycle-{cycle_no}-emit-denied",
            )
            print(f"cycle={cycle_no} case={case_id} denied")

        sleep(0.2)

    cp.close_session(
        session_id,
        payload={"summary": "multi-agent continuous demo completed"},
        command_id="multi-close-session",
    )

    print("events_recorded=", len(cp.replay(session_id)))
    print("db_path=", db_path)


if __name__ == "__main__":
    main()
