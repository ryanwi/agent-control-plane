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
from agent_control_plane.types.enums import EventKind
from agent_control_plane.types.proposals import ActionProposal
from examples.governance_demo_common import (
    GovernanceDecision,
    apply_governance_decision,
    parse_governance_decision,
)

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


async def _get_claude_decision(case: CaseInput) -> GovernanceDecision:
    assert query is not None
    assert ClaudeAgentOptions is not None

    prompt = (
        f"Case {case.case_id} priority={case.priority}. "
        f"Request: {case.summary}. "
        "Return exactly one word: APPROVE or DENY."
    )

    last_text = GovernanceDecision.DENY.value
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
    return parse_governance_decision(last_text)


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

        outcome = apply_governance_decision(
            cp=cp,
            session_id=session_id,
            proposal=proposal,
            ticket_id=ticket.id,
            decision=model_decision,
            provider="claude_agent_sdk",
            decided_by="claude-triage",
            agent_id="claude-agent-sdk",
            reason=f"Model returned {model_decision.value}",
            command_prefix=f"claude-cycle-{idx}",
        )
        print(f"{case.case_id}: {outcome}")

    cp.close_session(
        session_id,
        payload={"summary": "claude agent sdk integration demo completed"},
        command_id="claude-demo-close-session",
    )

    print("events_recorded=", len(cp.replay(session_id)))
    print("db_path=", db_path)


if __name__ == "__main__":
    asyncio.run(main())
