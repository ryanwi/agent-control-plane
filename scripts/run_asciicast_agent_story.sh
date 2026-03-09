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

    def run_once(self) -> None:
        session_id = self.control_plane.open_session(
            "support-agent-demo",
            max_cost=Decimal("20.00"),
            max_action_count=3,
            command_id="story-open-session",
        )
        print(f"session_id={session_id}")

        proposal = ActionProposal(
            session_id=session_id,
            resource_id="ticket-9001",
            resource_type="support_ticket",
            decision="status",
            reasoning="Fetch latest status for customer",
            metadata={"priority": "high", "source": "terminal-story"},
            weight=Decimal("0.75"),
            score=Decimal("0.88"),
        )
        proposal = self.control_plane.create_proposal(proposal, command_id="story-create-proposal")
        print(f"proposal_id={proposal.id}")

        ticket = self.control_plane.create_ticket(
            session_id,
            proposal.id,
            timeout_at=datetime.now(UTC) + timedelta(minutes=5),
            command_id="story-create-ticket",
        )
        print(f"ticket_id={ticket.id}")

        self.control_plane.approve_ticket(
            ticket.id,
            decided_by="operator-demo",
            reason="Approved for demo",
            decision_type=ApprovalDecisionType.ALLOW_ONCE,
            command_id="story-approve-ticket",
        )

        if self.control_plane.check_budget(session_id, cost=proposal.weight, action_count=1):
            self.control_plane.increment_budget(session_id, cost=proposal.weight, action_count=1)

        self.control_plane.emit_app(
            session_id,
            "agent_started",
            {"agent": "support", "proposal_id": str(proposal.id)},
            state_bearing=True,
        )
        self.control_plane.emit(
            session_id,
            EventKind.EXECUTION_COMPLETED,
            {"proposal_id": str(proposal.id), "status": "ok"},
            state_bearing=True,
            agent_id="support-agent",
            command_id="story-emit-execution",
        )

        result = self.control_plane.close_session(
            session_id,
            payload={"summary": "agent cycle completed"},
            command_id="story-close-session",
        )
        print(f"final_status={result.session.status}")


if __name__ == "__main__":
    db = Path("DB_PATH_PLACEHOLDER")
    db.unlink(missing_ok=True)
    agent = SupportAgent(db)
    agent.run_once()
PY

sed -i.bak "s|DB_PATH_PLACEHOLDER|${DB_PATH}|g" "${AGENT_FILE}" && rm -f "${AGENT_FILE}.bak"

echo
say_step "Agent source"
say_what "Show generated code that opens a session, proposes action, approves, emits events, closes."
say_why "Viewers can audit the exact agent logic before execution."
printf "%s$ nl -ba %s%s\n" "${C_CMD}" "${AGENT_FILE}" "${C_RESET}"
while IFS= read -r line; do
  printf "%s\n" "$line"
  sleep "${STEP1_LINE_PAUSE_SECONDS}"
done < <(nl -ba "${AGENT_FILE}")
pause_step1

echo
say_step "Step 2: Run the agent"
say_what "Execute one agent cycle through control-plane governance."
say_why "Demonstrate runtime behavior and returned IDs/status."
pause
run_cmd uv run python "${AGENT_FILE}"
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
say_step "[action_proposals]"
say_what "Action candidate produced by the agent and its governance status."
printf "%s$ sqlite3 %s <<'SQL'%s\n" "${C_CMD}" "${DB_PATH}" "${C_RESET}"
sqlite3 "${DB_PATH}" <<'SQL'
.headers on
.mode column
SELECT id, session_id, resource_id, decision, status, action_tier, risk_level
FROM action_proposals
ORDER BY created_at;
SQL
pause

echo
say_step "[approval_tickets]"
say_what "Human/automation approval decision persisted with scope metadata."
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
