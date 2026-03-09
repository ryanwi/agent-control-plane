"""ACP + Claude Agent SDK integration demo.

Run:
    uv run python examples/claude_agent_sdk_integration.py

Prerequisites:
    uv pip install claude-agent-sdk
    export ANTHROPIC_API_KEY=...
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from agent_control_plane.sync import ControlPlaneFacade, DictEventMapper
from agent_control_plane.types.enums import ApprovalDecisionType, EventKind
from agent_control_plane.types.proposals import ActionProposal

try:
    from claude_agent_sdk import ClaudeAgentOptions, query
except ModuleNotFoundError:  # pragma: no cover - optional dependency path
    ClaudeAgentOptions = None  # type: ignore[assignment]
    query = None  # type: ignore[assignment]


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


async def _get_claude_decision(case: CaseInput) -> str:
    assert query is not None
    assert ClaudeAgentOptions is not None

    prompt = (
        f"Case {case.case_id} priority={case.priority}. "
        f"Request: {case.summary}. "
        "Return exactly one word: APPROVE or DENY."
    )

    last_text = "DENY"
    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(allowed_tools=[]),
    ):
        result = getattr(message, "result", None)
        if isinstance(result, str) and result.strip():
            last_text = result
        else:
            msg_text = str(message).strip()
            if msg_text:
                last_text = msg_text
    return _parse_decision(last_text)


async def main() -> None:
    if ClaudeAgentOptions is None or query is None:
        raise SystemExit(
            "Missing optional dependency 'claude-agent-sdk'. Install with: uv pip install claude-agent-sdk"
        )
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY is not set")

    db_path = Path("./control_plane_claude_agents_demo.db")
    db_path.unlink(missing_ok=True)

    mapper = DictEventMapper({"loop_started": EventKind.CYCLE_STARTED, "loop_finished": EventKind.CYCLE_COMPLETED})
    cp = ControlPlaneFacade.from_database_url(f"sqlite:///{db_path}", mapper=mapper)
    cp.setup()

    session_id = cp.open_session(
        "claude-agent-sdk-demo",
        max_cost=Decimal("15.00"),
        max_action_count=4,
        command_id="claude-demo-open-session",
    )
    print(f"session_id={session_id}")

    cases = [
        CaseInput("case-2001", "high", "Customer asks for status plus sensitive internal notes."),
        CaseInput("case-2002", "low", "Customer asks for standard status update."),
    ]

    for idx, case in enumerate(cases, start=1):
        model_decision = await _get_claude_decision(case)
        should_approve = model_decision == "APPROVE"

        proposal = ActionProposal(
            session_id=session_id,
            resource_id=case.case_id,
            resource_type="customer_case",
            decision="status",
            reasoning=f"Claude triage decision={model_decision}",
            metadata={"provider": "claude_agent_sdk", "cycle": idx, "priority": case.priority},
            weight=Decimal("0.50"),
            score=Decimal("0.70"),
        )
        proposal = cp.create_proposal(proposal, command_id=f"claude-cycle-{idx}-proposal")

        ticket = cp.create_ticket(
            session_id,
            proposal.id,
            timeout_at=datetime.now(UTC) + timedelta(minutes=5),
            command_id=f"claude-cycle-{idx}-ticket",
        )

        if should_approve:
            cp.approve_ticket(
                ticket.id,
                decided_by="claude-triage",
                reason=f"Model returned {model_decision}",
                decision_type=ApprovalDecisionType.ALLOW_ONCE,
                command_id=f"claude-cycle-{idx}-approve",
            )
            cp.emit(
                session_id,
                EventKind.APPROVAL_GRANTED,
                {"case_id": case.case_id, "decision": model_decision, "provider": "claude_agent_sdk"},
                state_bearing=True,
                command_id=f"claude-cycle-{idx}-emit-approval-granted",
            )
            cp.emit(
                session_id,
                EventKind.EXECUTION_COMPLETED,
                {"case_id": case.case_id, "result": "status sent"},
                state_bearing=True,
                agent_id="claude-agent-sdk",
                command_id=f"claude-cycle-{idx}-emit-executed",
            )
            print(f"{case.case_id}: APPROVED")
        else:
            cp.deny_ticket(
                ticket.id,
                reason=f"Model returned {model_decision}",
                command_id=f"claude-cycle-{idx}-deny",
            )
            cp.emit(
                session_id,
                EventKind.APPROVAL_DENIED,
                {"case_id": case.case_id, "decision": model_decision, "provider": "claude_agent_sdk"},
                state_bearing=True,
                agent_id="claude-agent-sdk",
                command_id=f"claude-cycle-{idx}-emit-approval-denied",
            )
            print(f"{case.case_id}: DENIED")

    cp.close_session(
        session_id,
        payload={"summary": "claude agent sdk integration demo completed"},
        command_id="claude-demo-close-session",
    )

    print("events_recorded=", len(cp.replay(session_id)))
    print("db_path=", db_path)


if __name__ == "__main__":
    asyncio.run(main())
