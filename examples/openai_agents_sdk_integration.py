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
from agent_control_plane.types.enums import EventKind
from agent_control_plane.types.proposals import ActionProposal
from examples.governance_demo_common import (
    GovernanceDecision,
    apply_governance_decision,
    parse_governance_decision,
)

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
        model_decision = parse_governance_decision(str(run_result.final_output))

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

        outcome = apply_governance_decision(
            cp=cp,
            session_id=session_id,
            proposal=proposal,
            ticket_id=ticket.id,
            decision=GovernanceDecision(model_decision.value),
            provider="openai_agents_sdk",
            decided_by="openai-triage",
            agent_id="openai-agents-sdk",
            reason=f"Model returned {model_decision.value}",
            command_prefix=f"openai-cycle-{idx}",
        )
        print(f"{case.case_id}: {outcome}")

    cp.close_session(
        session_id,
        payload={"summary": "openai agents sdk integration demo completed"},
        command_id="openai-demo-close-session",
    )

    print("events_recorded=", len(cp.replay(session_id)))
    print("db_path=", db_path)


if __name__ == "__main__":
    main()
