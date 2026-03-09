"""ACP + Google ADK multi-agent integration demo.

Run:
    uv run python examples/google_adk_integration.py

Prerequisites for real ADK graph composition:
    uv pip install google-adk
    export GOOGLE_API_KEY=...

Notes:
- This demo builds ADK agent graph objects when ADK is installed.
- Governance and persistence are enforced via ACP regardless of orchestration layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from agent_control_plane.sync import ControlPlaneFacade, DictEventMapper
from agent_control_plane.types.enums import ApprovalDecisionType, EventKind
from agent_control_plane.types.proposals import ActionProposal

try:
    from google.adk.agents import LlmAgent, SequentialAgent
except ModuleNotFoundError:  # pragma: no cover - optional dependency path
    LlmAgent = None  # type: ignore[assignment]
    SequentialAgent = None  # type: ignore[assignment]


@dataclass(frozen=True)
class CaseInput:
    case_id: str
    priority: str
    summary: str


def _build_adk_graph() -> object | None:
    if LlmAgent is None or SequentialAgent is None:
        return None

    triage = LlmAgent(
        name="TriageAgent",
        instruction="Classify case risk and output approve/deny recommendation.",
        output_key="triage_decision",
    )
    checker = LlmAgent(
        name="PolicyChecker",
        instruction="Check policy boundaries and output final approve/deny recommendation.",
        output_key="policy_decision",
    )
    return SequentialAgent(name="CaseGovernancePipeline", sub_agents=[triage, checker])


def _decision_from_case(case: CaseInput) -> str:
    return "DENY" if case.priority.lower() == "high" else "APPROVE"


def main() -> None:
    db_path = Path("./control_plane_google_adk_demo.db")
    db_path.unlink(missing_ok=True)

    adk_graph = _build_adk_graph()
    print(f"adk_graph_built={adk_graph is not None}")

    mapper = DictEventMapper({"loop_started": EventKind.CYCLE_STARTED, "loop_finished": EventKind.CYCLE_COMPLETED})
    cp = ControlPlaneFacade.from_database_url(f"sqlite:///{db_path}", mapper=mapper)
    cp.setup()

    session_id = cp.open_session(
        "google-adk-integration-demo",
        max_cost=Decimal("15.00"),
        max_action_count=4,
        command_id="adk-demo-open-session",
    )
    print(f"session_id={session_id}")

    cases = [
        CaseInput("case-3001", "high", "Customer asks for status plus confidential internal notes."),
        CaseInput("case-3002", "low", "Customer asks for standard case status."),
    ]

    for idx, case in enumerate(cases, start=1):
        decision = _decision_from_case(case)
        should_approve = decision == "APPROVE"

        proposal = ActionProposal(
            session_id=session_id,
            resource_id=case.case_id,
            resource_type="customer_case",
            decision="status",
            reasoning=f"Google ADK pipeline decision={decision}",
            metadata={"provider": "google_adk", "cycle": idx, "priority": case.priority},
            weight=Decimal("0.50"),
            score=Decimal("0.70"),
        )
        proposal = cp.create_proposal(proposal, command_id=f"adk-cycle-{idx}-proposal")

        ticket = cp.create_ticket(
            session_id,
            proposal.id,
            timeout_at=datetime.now(UTC) + timedelta(minutes=5),
            command_id=f"adk-cycle-{idx}-ticket",
        )

        if should_approve:
            cp.approve_ticket(
                ticket.id,
                decided_by="google-adk-pipeline",
                reason=f"Pipeline returned {decision}",
                decision_type=ApprovalDecisionType.ALLOW_ONCE,
                command_id=f"adk-cycle-{idx}-approve",
            )
            cp.emit(
                session_id,
                EventKind.APPROVAL_GRANTED,
                {"case_id": case.case_id, "decision": decision, "provider": "google_adk"},
                state_bearing=True,
                command_id=f"adk-cycle-{idx}-emit-granted",
            )
            cp.emit(
                session_id,
                EventKind.EXECUTION_COMPLETED,
                {"case_id": case.case_id, "result": "status sent"},
                state_bearing=True,
                agent_id="google-adk",
                command_id=f"adk-cycle-{idx}-emit-executed",
            )
            print(f"{case.case_id}: APPROVED")
        else:
            cp.deny_ticket(
                ticket.id,
                reason=f"Pipeline returned {decision}",
                command_id=f"adk-cycle-{idx}-deny",
            )
            cp.emit(
                session_id,
                EventKind.APPROVAL_DENIED,
                {"case_id": case.case_id, "decision": decision, "provider": "google_adk"},
                state_bearing=True,
                agent_id="google-adk",
                command_id=f"adk-cycle-{idx}-emit-denied",
            )
            print(f"{case.case_id}: DENIED")

    cp.close_session(
        session_id,
        payload={"summary": "google adk integration demo completed"},
        command_id="adk-demo-close-session",
    )

    print("events_recorded=", len(cp.replay(session_id)))
    print("db_path=", db_path)


if __name__ == "__main__":
    main()
