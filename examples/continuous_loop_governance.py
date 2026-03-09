"""Continuous-loop governance demo (denied + approved outcomes).

Run:
    uv run python examples/continuous_loop_governance.py
"""

from __future__ import annotations

from argparse import ArgumentParser
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from time import sleep
from uuid import UUID

from agent_control_plane.sync import ControlPlaneFacade as ControlPlaneClient
from agent_control_plane.sync import DictEventMapper
from agent_control_plane.types.enums import ApprovalDecisionType, EventKind
from agent_control_plane.types.proposals import ActionProposal


class SupportAgent:
    def __init__(self, db_path: Path) -> None:
        mapper = DictEventMapper(
            {
                "agent_started": EventKind.CYCLE_STARTED,
                "agent_finished": EventKind.CYCLE_COMPLETED,
            }
        )
        self.control_plane = ControlPlaneClient.from_database_url(f"sqlite:///{db_path}", mapper=mapper)
        self.control_plane.setup()

    def process_case(self, *, session_id: UUID, case_id: str, cycle_no: int, should_approve: bool) -> None:
        cycle_tag = f"cycle-{cycle_no}"

        proposal = ActionProposal(
            session_id=session_id,
            resource_id=case_id,
            resource_type="customer_case",
            decision="status",
            reasoning="Fetch latest status for customer case",
            metadata={"priority": "high", "source": "terminal-story", "cycle": cycle_no},
            weight=Decimal("0.75"),
            score=Decimal("0.88"),
        )
        proposal = self.control_plane.create_proposal(proposal, command_id=f"story-{cycle_tag}-create-proposal")
        print(f"proposal_id={proposal.id}")
        print(f"approval_request=allow decision={proposal.decision} on {proposal.resource_type}:{proposal.resource_id}")

        approval_ticket = self.control_plane.create_ticket(
            session_id,
            proposal.id,
            timeout_at=datetime.now(UTC) + timedelta(minutes=5),
            command_id=f"story-{cycle_tag}-create-ticket",
        )
        print(f"approval_ticket_id={approval_ticket.id}")

        if not should_approve:
            denied_ticket = self.control_plane.deny_ticket(
                approval_ticket.id,
                reason="Denied in demo cycle",
                command_id=f"story-{cycle_tag}-deny-ticket",
            )
            self.control_plane.emit(
                session_id,
                EventKind.APPROVAL_DENIED,
                {"proposal_id": str(proposal.id), "resource_id": proposal.resource_id, "cycle": cycle_no},
                state_bearing=True,
                agent_id="support-agent",
                command_id=f"story-{cycle_tag}-emit-approval-denied",
            )
            print(f"approval_status={denied_ticket.status}")
            print(
                f"approval_denied_for=decision={proposal.decision} on {proposal.resource_type}:{proposal.resource_id}"
            )
            return

        approved_ticket = self.control_plane.approve_ticket(
            approval_ticket.id,
            decided_by="operator-demo",
            reason="Approved for demo",
            decision_type=ApprovalDecisionType.ALLOW_ONCE,
            command_id=f"story-{cycle_tag}-approve-ticket",
        )
        self.control_plane.emit(
            session_id,
            EventKind.APPROVAL_GRANTED,
            {"proposal_id": str(proposal.id), "resource_id": proposal.resource_id, "cycle": cycle_no},
            state_bearing=True,
            agent_id="support-agent",
            command_id=f"story-{cycle_tag}-emit-approval-granted",
        )
        print(f"approval_status={approved_ticket.status}")
        print(f"approval_granted_for=decision={proposal.decision} on {proposal.resource_type}:{proposal.resource_id}")

        if self.control_plane.check_budget(session_id, cost=proposal.weight, action_count=1):
            self.control_plane.increment_budget(session_id, cost=proposal.weight, action_count=1)

        self.control_plane.emit_app(
            session_id,
            "agent_started",
            {"agent": "support", "proposal_id": str(proposal.id), "cycle": cycle_no},
            state_bearing=True,
        )
        self.control_plane.emit(
            session_id,
            EventKind.EXECUTION_COMPLETED,
            {"proposal_id": str(proposal.id), "status": "ok", "cycle": cycle_no},
            state_bearing=True,
            agent_id="support-agent",
            command_id=f"story-{cycle_tag}-emit-execution",
        )
        print("execution_status=completed")

    def run_forever(self, *, max_cycles: int = 2, loop_sleep_seconds: float = 0.3) -> None:
        session_id = self.control_plane.open_session(
            "support-agent-demo",
            max_cost=Decimal("20.00"),
            max_action_count=3,
            command_id="story-open-session",
        )
        print(f"session_id={session_id}")
        print(f"loop_mode=continuous (demo bounded to {max_cycles} cycles)")

        cycle_plan = [
            ("case-9001", False),
            ("case-9002", True),
        ]
        for cycle_no, (case_id, should_approve) in enumerate(cycle_plan, start=1):
            if cycle_no > max_cycles:
                break
            print(f"loop_cycle={cycle_no} case_id={case_id} should_approve={should_approve}")
            self.process_case(
                session_id=session_id,
                case_id=case_id,
                cycle_no=cycle_no,
                should_approve=should_approve,
            )
            sleep(loop_sleep_seconds)

        result = self.control_plane.close_session(
            session_id,
            payload={"summary": "continuous loop demo completed", "cycles": max_cycles},
            command_id="story-close-session",
        )
        print(f"final_status={result.session.status}")


def main() -> None:
    parser = ArgumentParser(description="Run continuous-loop governance demo")
    parser.add_argument("--db", default="./continuous_loop_demo.db", help="SQLite database path")
    parser.add_argument("--max-cycles", type=int, default=2, help="Number of loop cycles to run")
    parser.add_argument(
        "--loop-sleep-seconds",
        type=float,
        default=0.3,
        help="Pause between loop cycles",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    db_path.unlink(missing_ok=True)
    agent = SupportAgent(db_path)
    agent.run_forever(max_cycles=args.max_cycles, loop_sleep_seconds=args.loop_sleep_seconds)


if __name__ == "__main__":
    main()
