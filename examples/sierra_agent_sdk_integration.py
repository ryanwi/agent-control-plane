"""ACP + Sierra Agent SDK adapter demo.

Run:
    uv run python examples/sierra_agent_sdk_integration.py

Configuration:
    SIERRA_AGENT_SDK_URL=...   # optional HTTP endpoint for decision adapter

Notes:
- Sierra public docs are high-level product docs; API shape is deployment-specific.
- This demo uses a minimal adapter contract: POST case payload, read {"decision": "APPROVE|DENY"}.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from agent_control_plane.sync import ControlPlaneFacade, DictEventMapper
from agent_control_plane.types.enums import ApprovalDecisionType, EventKind
from agent_control_plane.types.proposals import ActionProposal


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


def _decision_from_sierra(case: CaseInput) -> str:
    endpoint = os.getenv("SIERRA_AGENT_SDK_URL")
    if not endpoint:
        return "DENY" if case.priority.lower() == "high" else "APPROVE"

    payload = {
        "resource_id": case.case_id,
        "resource_type": "customer_case",
        "priority": case.priority,
        "summary": case.summary,
    }
    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            body = response.read().decode("utf-8")
    except URLError:
        return "DENY"

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return "DENY"

    decision = parsed.get("decision", "DENY") if isinstance(parsed, dict) else "DENY"
    return _parse_decision(str(decision))


def main() -> None:
    db_path = Path("./control_plane_sierra_demo.db")
    db_path.unlink(missing_ok=True)

    mapper = DictEventMapper({"loop_started": EventKind.CYCLE_STARTED, "loop_finished": EventKind.CYCLE_COMPLETED})
    cp = ControlPlaneFacade.from_database_url(f"sqlite:///{db_path}", mapper=mapper)
    cp.setup()

    session_id = cp.open_session(
        "sierra-integration-demo",
        max_cost=Decimal("15.00"),
        max_action_count=4,
        command_id="sierra-demo-open-session",
    )
    print(f"session_id={session_id}")

    cases = [
        CaseInput("case-5001", "high", "Customer asks for status plus policy-restricted account change."),
        CaseInput("case-5002", "low", "Customer asks for standard status update."),
    ]

    for idx, case in enumerate(cases, start=1):
        decision = _decision_from_sierra(case)
        should_approve = decision == "APPROVE"

        proposal = ActionProposal(
            session_id=session_id,
            resource_id=case.case_id,
            resource_type="customer_case",
            decision="status",
            reasoning=f"Sierra adapter decision={decision}",
            metadata={"provider": "sierra", "cycle": idx, "priority": case.priority},
            weight=Decimal("0.50"),
            score=Decimal("0.70"),
        )
        proposal = cp.create_proposal(proposal, command_id=f"sierra-cycle-{idx}-proposal")

        ticket = cp.create_ticket(
            session_id,
            proposal.id,
            timeout_at=datetime.now(UTC) + timedelta(minutes=5),
            command_id=f"sierra-cycle-{idx}-ticket",
        )

        if should_approve:
            cp.approve_ticket(
                ticket.id,
                decided_by="sierra-adapter",
                reason=f"Adapter returned {decision}",
                decision_type=ApprovalDecisionType.ALLOW_ONCE,
                command_id=f"sierra-cycle-{idx}-approve",
            )
            cp.emit(
                session_id,
                EventKind.APPROVAL_GRANTED,
                {"case_id": case.case_id, "decision": decision, "provider": "sierra"},
                state_bearing=True,
                command_id=f"sierra-cycle-{idx}-emit-granted",
            )
            cp.emit(
                session_id,
                EventKind.EXECUTION_COMPLETED,
                {"case_id": case.case_id, "result": "status sent"},
                state_bearing=True,
                agent_id="sierra-adapter",
                command_id=f"sierra-cycle-{idx}-emit-executed",
            )
            print(f"{case.case_id}: APPROVED")
        else:
            cp.deny_ticket(
                ticket.id,
                reason=f"Adapter returned {decision}",
                command_id=f"sierra-cycle-{idx}-deny",
            )
            cp.emit(
                session_id,
                EventKind.APPROVAL_DENIED,
                {"case_id": case.case_id, "decision": decision, "provider": "sierra"},
                state_bearing=True,
                agent_id="sierra-adapter",
                command_id=f"sierra-cycle-{idx}-emit-denied",
            )
            print(f"{case.case_id}: DENIED")

    cp.close_session(
        session_id,
        payload={"summary": "sierra integration demo completed"},
        command_id="sierra-demo-close-session",
    )

    print("events_recorded=", len(cp.replay(session_id)))
    print("db_path=", db_path)


if __name__ == "__main__":
    main()
