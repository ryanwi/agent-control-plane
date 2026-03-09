"""ACP + LangChain multi-agent integration demo.

Run:
    uv run python examples/langchain_multi_agent_integration.py

Optional prerequisites for model-backed decisions:
    uv pip install langchain "langchain[openai]"
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
    from langchain.agents import create_agent
    from langchain.chat_models import init_chat_model
except ModuleNotFoundError:  # pragma: no cover - optional dependency path
    create_agent = None  # type: ignore[assignment]
    init_chat_model = None  # type: ignore[assignment]


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


def _langchain_decision(case: CaseInput) -> str:
    if create_agent is None or init_chat_model is None or not os.getenv("OPENAI_API_KEY"):
        return "DENY" if case.priority.lower() == "high" else "APPROVE"

    model = init_chat_model("gpt-4.1-mini")
    supervisor = create_agent(
        model=model,
        tools=[],
        system_prompt=(
            "You are a supervisor agent. "
            "Return exactly one word: APPROVE or DENY. "
            "Deny risky support actions; approve low-risk status-only actions."
        ),
    )
    result = supervisor.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"Case {case.case_id} priority={case.priority}. "
                        f"Request: {case.summary}. Return APPROVE or DENY only."
                    ),
                }
            ]
        }
    )

    messages = result.get("messages", []) if isinstance(result, dict) else []
    final_text = str(messages[-1].content) if messages else "DENY"
    return _parse_decision(final_text)


def main() -> None:
    db_path = Path("./control_plane_langchain_demo.db")
    db_path.unlink(missing_ok=True)

    mapper = DictEventMapper({"loop_started": EventKind.CYCLE_STARTED, "loop_finished": EventKind.CYCLE_COMPLETED})
    cp = ControlPlaneFacade.from_database_url(f"sqlite:///{db_path}", mapper=mapper)
    cp.setup()

    session_id = cp.open_session(
        "langchain-integration-demo",
        max_cost=Decimal("15.00"),
        max_action_count=4,
        command_id="langchain-demo-open-session",
    )
    print(f"session_id={session_id}")

    cases = [
        CaseInput("case-4001", "high", "Customer requests status plus a hidden internal escalation note."),
        CaseInput("case-4002", "low", "Customer requests a standard status update."),
    ]

    for idx, case in enumerate(cases, start=1):
        decision = _langchain_decision(case)
        should_approve = decision == "APPROVE"

        proposal = ActionProposal(
            session_id=session_id,
            resource_id=case.case_id,
            resource_type="customer_case",
            decision="status",
            reasoning=f"LangChain supervisor decision={decision}",
            metadata={"provider": "langchain", "cycle": idx, "priority": case.priority},
            weight=Decimal("0.50"),
            score=Decimal("0.70"),
        )
        proposal = cp.create_proposal(proposal, command_id=f"langchain-cycle-{idx}-proposal")

        ticket = cp.create_ticket(
            session_id,
            proposal.id,
            timeout_at=datetime.now(UTC) + timedelta(minutes=5),
            command_id=f"langchain-cycle-{idx}-ticket",
        )

        if should_approve:
            cp.approve_ticket(
                ticket.id,
                decided_by="langchain-supervisor",
                reason=f"Supervisor returned {decision}",
                decision_type=ApprovalDecisionType.ALLOW_ONCE,
                command_id=f"langchain-cycle-{idx}-approve",
            )
            cp.emit(
                session_id,
                EventKind.APPROVAL_GRANTED,
                {"case_id": case.case_id, "decision": decision, "provider": "langchain"},
                state_bearing=True,
                command_id=f"langchain-cycle-{idx}-emit-granted",
            )
            cp.emit(
                session_id,
                EventKind.EXECUTION_COMPLETED,
                {"case_id": case.case_id, "result": "status sent"},
                state_bearing=True,
                agent_id="langchain-supervisor",
                command_id=f"langchain-cycle-{idx}-emit-executed",
            )
            print(f"{case.case_id}: APPROVED")
        else:
            cp.deny_ticket(
                ticket.id,
                reason=f"Supervisor returned {decision}",
                command_id=f"langchain-cycle-{idx}-deny",
            )
            cp.emit(
                session_id,
                EventKind.APPROVAL_DENIED,
                {"case_id": case.case_id, "decision": decision, "provider": "langchain"},
                state_bearing=True,
                agent_id="langchain-supervisor",
                command_id=f"langchain-cycle-{idx}-emit-denied",
            )
            print(f"{case.case_id}: DENIED")

    cp.close_session(
        session_id,
        payload={"summary": "langchain integration demo completed"},
        command_id="langchain-demo-close-session",
    )

    print("events_recorded=", len(cp.replay(session_id)))
    print("db_path=", db_path)


if __name__ == "__main__":
    main()
