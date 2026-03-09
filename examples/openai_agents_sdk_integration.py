"""ACP + OpenAI Agents SDK integration demo.

Run:
    uv run python examples/openai_agents_sdk_integration.py

Prerequisites:
    uv pip install openai-agents
    export OPENAI_API_KEY=...
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from agent_control_plane.sync import ControlPlaneFacade, DictEventMapper
from agent_control_plane.types.enums import ApprovalDecisionType, EventKind
from agent_control_plane.types.proposals import ActionProposal

try:
    from agents import Agent, Runner
except ModuleNotFoundError:  # pragma: no cover - optional dependency path
    Agent = None  # type: ignore[assignment]
    Runner = None  # type: ignore[assignment]


@dataclass(frozen=True)
class CaseInput:
    case_id: str
    priority: str
    summary: str


def _parse_decision(text: str) -> str:
    normalized = text.strip().upper()
    if "DENY" in normalized:
        return "DENY"
    if "APPROVE" in normalized:
        return "APPROVE"
    return "DENY"


def _build_case_triage_agent() -> Agent:
    assert Agent is not None
    return Agent(
        name="CaseTriage",
        instructions=(
            "You are a strict support-governance triage agent. "
            "Return exactly one word: APPROVE or DENY. "
            "Deny high-risk requests and approve low-risk status checks."
        ),
    )


def main() -> None:
    if Agent is None or Runner is None:
        raise SystemExit("Missing optional dependency 'openai-agents'. Install with: uv pip install openai-agents")
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not set")

    db_path = Path("./control_plane_openai_agents_demo.db")
    db_path.unlink(missing_ok=True)

    mapper = DictEventMapper({"loop_started": EventKind.CYCLE_STARTED, "loop_finished": EventKind.CYCLE_COMPLETED})
    cp = ControlPlaneFacade.from_database_url(f"sqlite:///{db_path}", mapper=mapper)
    cp.setup()

    session_id = cp.open_session(
        "openai-agents-sdk-demo",
        max_cost=Decimal("15.00"),
        max_action_count=4,
        command_id="openai-demo-open-session",
    )
    print(f"session_id={session_id}")

    triage_agent = _build_case_triage_agent()
    cases = [
        CaseInput("case-1001", "high", "Customer requests a billing status update and full account export."),
        CaseInput("case-1002", "low", "Customer asks for latest ticket status only."),
    ]

    for idx, case in enumerate(cases, start=1):
        run_result = Runner.run_sync(
            triage_agent,
            (f"Case {case.case_id} priority={case.priority}. Request: {case.summary}. Return APPROVE or DENY only."),
        )
        model_decision = _parse_decision(str(run_result.final_output))
        should_approve = model_decision == "APPROVE"

        proposal = ActionProposal(
            session_id=session_id,
            resource_id=case.case_id,
            resource_type="customer_case",
            decision="status",
            reasoning=f"OpenAI triage decision={model_decision}",
            metadata={"provider": "openai_agents_sdk", "cycle": idx, "priority": case.priority},
            weight=Decimal("0.50"),
            score=Decimal("0.70"),
        )
        proposal = cp.create_proposal(proposal, command_id=f"openai-cycle-{idx}-proposal")

        ticket = cp.create_ticket(
            session_id,
            proposal.id,
            timeout_at=datetime.now(UTC) + timedelta(minutes=5),
            command_id=f"openai-cycle-{idx}-ticket",
        )

        if should_approve:
            cp.approve_ticket(
                ticket.id,
                decided_by="openai-triage",
                reason=f"Model returned {model_decision}",
                decision_type=ApprovalDecisionType.ALLOW_ONCE,
                command_id=f"openai-cycle-{idx}-approve",
            )
            cp.emit(
                session_id,
                EventKind.APPROVAL_GRANTED,
                {"case_id": case.case_id, "decision": model_decision, "provider": "openai_agents_sdk"},
                state_bearing=True,
                command_id=f"openai-cycle-{idx}-emit-approval-granted",
            )
            cp.emit(
                session_id,
                EventKind.EXECUTION_COMPLETED,
                {"case_id": case.case_id, "result": "status sent"},
                state_bearing=True,
                agent_id="openai-agents-sdk",
                command_id=f"openai-cycle-{idx}-emit-executed",
            )
            print(f"{case.case_id}: APPROVED")
        else:
            cp.deny_ticket(
                ticket.id,
                reason=f"Model returned {model_decision}",
                command_id=f"openai-cycle-{idx}-deny",
            )
            cp.emit(
                session_id,
                EventKind.APPROVAL_DENIED,
                {"case_id": case.case_id, "decision": model_decision, "provider": "openai_agents_sdk"},
                state_bearing=True,
                agent_id="openai-agents-sdk",
                command_id=f"openai-cycle-{idx}-emit-approval-denied",
            )
            print(f"{case.case_id}: DENIED")

    cp.close_session(
        session_id,
        payload={"summary": "openai agents sdk integration demo completed"},
        command_id="openai-demo-close-session",
    )

    print("events_recorded=", len(cp.replay(session_id)))
    print("db_path=", db_path)


if __name__ == "__main__":
    main()
