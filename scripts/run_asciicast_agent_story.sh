#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEMO_DIR="$(mktemp -d /tmp/acp-agent-story-XXXXXX)"
AGENT_FILE="${DEMO_DIR}/support_agent_demo.py"
DB_PATH="${DEMO_DIR}/support_agent_demo.db"
PAUSE_SECONDS="${DEMO_PAUSE_SECONDS:-0.8}"
LINE_PAUSE_SECONDS="${DEMO_LINE_PAUSE_SECONDS:-0.03}"
STEP1_PAUSE_SECONDS="${DEMO_STEP1_PAUSE_SECONDS:-${PAUSE_SECONDS}}"
STEP1_LINE_PAUSE_SECONDS="${DEMO_STEP1_LINE_PAUSE_SECONDS:-0.08}"
COLOR_MODE="${DEMO_COLOR:-auto}"

if [[ "${COLOR_MODE}" == "1" || "${COLOR_MODE}" == "true" || ( "${COLOR_MODE}" == "auto" && -t 1 ) ]]; then
  C_RESET=$'\033[0m'
  C_HDR=$'\033[1;36m'
  C_WHAT=$'\033[1;33m'
  C_WHY=$'\033[0;32m'
  C_CMD=$'\033[0;35m'
else
  C_RESET=""
  C_HDR=""
  C_WHAT=""
  C_WHY=""
  C_CMD=""
fi

pause() {
  sleep "${PAUSE_SECONDS}"
}

pause_step1() {
  sleep "${STEP1_PAUSE_SECONDS}"
}

run_cmd() {
  printf "%s$ %s%s\n" "${C_CMD}" "$*" "${C_RESET}"
  "$@"
}

say_step() {
  printf "%s==> %s%s\n" "${C_HDR}" "$1" "${C_RESET}"
}

say_what() {
  printf "%sWHAT:%s %s\n" "${C_WHAT}" "${C_RESET}" "$1"
}

say_why() {
  printf "%sWHY:%s  %s\n" "${C_WHY}" "${C_RESET}" "$1"
}

if ! command -v sqlite3 >/dev/null 2>&1; then
  echo "sqlite3 is required for DB inspection output" >&2
  exit 1
fi

say_step "Demo workspace"
echo "${DEMO_DIR}"
pause

echo
say_step "Domain framing"
say_what "Client app resource: customer case (resource_id=case-9001, resource_type=customer_case)."
say_why "Control-plane approval_ticket is a separate governance record created to authorize that action."
pause

echo
say_step "Step 1: Create a Python agent file"
say_what "Write a minimal support agent that uses a control-plane client."
say_why "This proves integration starts with ordinary Python code, not framework magic."
pause_step1
printf "%s$ cat > %s <<'PY'  # (agent source shown below)%s\n" "${C_CMD}" "${AGENT_FILE}" "${C_RESET}"
cat > "${AGENT_FILE}" <<'PY'
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from time import sleep

from agent_control_plane.sync import ControlPlaneFacade as ControlPlaneClient, DictEventMapper
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

    def process_case(self, *, session_id, case_id: str, cycle_no: int, should_approve: bool) -> None:
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
        print(
            "approval_request="
            f"allow decision={proposal.decision} on {proposal.resource_type}:{proposal.resource_id}"
        )

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
                "approval_denied_for="
                f"decision={proposal.decision} on {proposal.resource_type}:{proposal.resource_id}"
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
        print(
            "approval_granted_for="
            f"decision={proposal.decision} on {proposal.resource_type}:{proposal.resource_id}"
        )

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
        print("loop_mode=continuous (demo bounded to 2 cycles)")

        cycle_plan = [
            ("case-9001", False),  # denied: shows governance blocking path
            ("case-9002", True),   # approved: shows execution path
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


if __name__ == "__main__":
    db = Path("DB_PATH_PLACEHOLDER")
    db.unlink(missing_ok=True)
    agent = SupportAgent(db)
    agent.run_forever()
PY

sed -i.bak "s|DB_PATH_PLACEHOLDER|${DB_PATH}|g" "${AGENT_FILE}" && rm -f "${AGENT_FILE}.bak"

echo
say_step "Agent source"
say_what "Show generated code for a continuous loop: one denied cycle, one approved cycle."
say_why "Viewers can audit both governance outcomes before execution."
printf "%s$ nl -ba %s%s\n" "${C_CMD}" "${AGENT_FILE}" "${C_RESET}"
while IFS= read -r line; do
  printf "%s\n" "$line"
  sleep "${STEP1_LINE_PAUSE_SECONDS}"
done < <(nl -ba "${AGENT_FILE}")
pause_step1

echo
say_step "Step 2: Run the agent"
say_what "Execute a continuous-style loop (bounded demo) through control-plane governance."
say_why "Demonstrate real-world behavior: blocked action first, then approved/executed action."
pause
run_cmd uv run python "${AGENT_FILE}"
say_what "Cycle 1 is denied; Cycle 2 is approved and executed on a customer_case."
pause

echo
say_step "Step 3: Show persisted control-plane data"
say_what "Inspect canonical tables written by the run."
say_why "Prove durability, auditability, and idempotent command tracking."
pause

echo
say_step "[tables]"
run_cmd sqlite3 "${DB_PATH}" '.tables'
pause

echo
say_step "[control_sessions]"
say_what "Session lifecycle and budget counters."
printf "%s$ sqlite3 %s <<'SQL'%s\n" "${C_CMD}" "${DB_PATH}" "${C_RESET}"
sqlite3 "${DB_PATH}" <<'SQL'
.headers on
.mode column
SELECT id, session_name, status, max_cost, used_cost, max_action_count, used_action_count
FROM control_sessions;
SQL
pause

echo
say_step "[action_proposals (client resource actions)]"
say_what "Rows here reference client-domain resources (customer cases), not control-plane approval records."
printf "%s$ sqlite3 %s <<'SQL'%s\n" "${C_CMD}" "${DB_PATH}" "${C_RESET}"
sqlite3 "${DB_PATH}" <<'SQL'
.headers on
.mode column
SELECT id, session_id, resource_id, resource_type, decision, status, action_tier, risk_level
FROM action_proposals
ORDER BY created_at;
SQL
pause

echo
say_step "[approval_tickets (control-plane governance records)]"
say_what "Rows here are control-plane approvals linked to proposals; they are not client customer cases."
printf "%s$ sqlite3 %s <<'SQL'%s\n" "${C_CMD}" "${DB_PATH}" "${C_RESET}"
sqlite3 "${DB_PATH}" <<'SQL'
.headers on
.mode column
SELECT id, session_id, proposal_id, status, decision_type, decided_by
FROM approval_tickets
ORDER BY created_at;
SQL
pause

echo
say_step "[approval_context (what was approved)]"
say_what "Join approvals to proposals to show the exact approved action on the client resource."
printf "%s$ sqlite3 %s <<'SQL'%s\n" "${C_CMD}" "${DB_PATH}" "${C_RESET}"
sqlite3 "${DB_PATH}" <<'SQL'
.headers on
.mode column
SELECT
  t.id AS approval_ticket_id,
  p.resource_type,
  p.resource_id,
  p.decision,
  t.status AS approval_status
FROM approval_tickets t
JOIN action_proposals p ON p.id = t.proposal_id
ORDER BY t.created_at;
SQL
pause

echo
say_step "[control_events]"
say_what "State-bearing event trail suitable for replay/projection."
printf "%s$ sqlite3 %s <<'SQL'%s\n" "${C_CMD}" "${DB_PATH}" "${C_RESET}"
sqlite3 "${DB_PATH}" <<'SQL'
.headers on
.mode column
SELECT session_id, seq, event_kind, state_bearing, agent_id
FROM control_events
ORDER BY seq;
SQL
pause

echo
say_step "[command_ledger]"
say_what "Idempotency records for mutating operations."
say_why "If retried with same command_id, writes are replay-safe."
printf "%s$ sqlite3 %s <<'SQL'%s\n" "${C_CMD}" "${DB_PATH}" "${C_RESET}"
sqlite3 "${DB_PATH}" <<'SQL'
.headers on
.mode column
SELECT command_id, operation, session_id
FROM command_ledger
ORDER BY created_at;
SQL
pause

echo
say_step "Demo artifacts"
echo "agent_file=${AGENT_FILE}"
echo "db_path=${DB_PATH}"
