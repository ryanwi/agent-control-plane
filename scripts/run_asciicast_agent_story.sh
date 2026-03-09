#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEMO_DIR="$(mktemp -d /tmp/acp-agent-story-XXXXXX)"
AGENT_FILE="${DEMO_DIR}/support_agent_demo.py"
DB_PATH="${DEMO_DIR}/support_agent_demo.db"
PAUSE_SECONDS="${DEMO_PAUSE_SECONDS:-0.8}"
LINE_PAUSE_SECONDS="${DEMO_LINE_PAUSE_SECONDS:-0.03}"

pause() {
  sleep "${PAUSE_SECONDS}"
}

run_cmd() {
  echo "\$ $*"
  "$@"
}

if ! command -v sqlite3 >/dev/null 2>&1; then
  echo "sqlite3 is required for DB inspection output" >&2
  exit 1
fi

echo "==> Demo workspace"
echo "${DEMO_DIR}"
pause

echo
echo "==> Step 1: Create a Python agent file"
pause
echo "\$ cat > ${AGENT_FILE} <<'PY'  # (agent source shown below)"
cat > "${AGENT_FILE}" <<'PY'
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from agent_control_plane.sync import ControlPlaneFacade, DictEventMapper
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
        self.facade = ControlPlaneFacade.from_database_url(f"sqlite:///{db_path}", mapper=mapper)
        self.facade.setup()

    def run_once(self) -> None:
        session_id = self.facade.open_session(
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
        proposal = self.facade.create_proposal(proposal, command_id="story-create-proposal")
        print(f"proposal_id={proposal.id}")

        ticket = self.facade.create_ticket(
            session_id,
            proposal.id,
            timeout_at=datetime.now(UTC) + timedelta(minutes=5),
            command_id="story-create-ticket",
        )
        print(f"ticket_id={ticket.id}")

        self.facade.approve_ticket(
            ticket.id,
            decided_by="operator-demo",
            reason="Approved for demo",
            decision_type=ApprovalDecisionType.ALLOW_ONCE,
            command_id="story-approve-ticket",
        )

        if self.facade.check_budget(session_id, cost=proposal.weight, action_count=1):
            self.facade.increment_budget(session_id, cost=proposal.weight, action_count=1)

        self.facade.emit_app(
            session_id,
            "agent_started",
            {"agent": "support", "proposal_id": str(proposal.id)},
            state_bearing=True,
        )
        self.facade.emit(
            session_id,
            EventKind.EXECUTION_COMPLETED,
            {"proposal_id": str(proposal.id), "status": "ok"},
            state_bearing=True,
            agent_id="support-agent",
            command_id="story-emit-execution",
        )

        result = self.facade.close_session(
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
echo "==> Agent source"
echo "\$ nl -ba ${AGENT_FILE}"
while IFS= read -r line; do
  printf "%s\n" "$line"
  sleep "${LINE_PAUSE_SECONDS}"
done < <(nl -ba "${AGENT_FILE}")
pause

echo
echo "==> Step 2: Run the agent"
pause
run_cmd uv run python "${AGENT_FILE}"
pause

echo
echo "==> Step 3: Show persisted control-plane data"
pause

echo
echo "[tables]"
run_cmd sqlite3 "${DB_PATH}" '.tables'
pause

echo
echo "[control_sessions]"
echo "\$ sqlite3 ${DB_PATH} <<'SQL'"
sqlite3 "${DB_PATH}" <<'SQL'
.headers on
.mode column
SELECT id, session_name, status, max_cost, used_cost, max_action_count, used_action_count
FROM control_sessions;
SQL
pause

echo
echo "[action_proposals]"
echo "\$ sqlite3 ${DB_PATH} <<'SQL'"
sqlite3 "${DB_PATH}" <<'SQL'
.headers on
.mode column
SELECT id, session_id, resource_id, decision, status, action_tier, risk_level
FROM action_proposals
ORDER BY created_at;
SQL
pause

echo
echo "[approval_tickets]"
echo "\$ sqlite3 ${DB_PATH} <<'SQL'"
sqlite3 "${DB_PATH}" <<'SQL'
.headers on
.mode column
SELECT id, session_id, proposal_id, status, decision_type, decided_by
FROM approval_tickets
ORDER BY created_at;
SQL
pause

echo
echo "[control_events]"
echo "\$ sqlite3 ${DB_PATH} <<'SQL'"
sqlite3 "${DB_PATH}" <<'SQL'
.headers on
.mode column
SELECT session_id, seq, event_kind, state_bearing, agent_id
FROM control_events
ORDER BY seq;
SQL
pause

echo
echo "[command_ledger]"
echo "\$ sqlite3 ${DB_PATH} <<'SQL'"
sqlite3 "${DB_PATH}" <<'SQL'
.headers on
.mode column
SELECT command_id, operation, session_id
FROM command_ledger
ORDER BY created_at;
SQL
pause

echo
echo "==> Demo artifacts"
echo "agent_file=${AGENT_FILE}"
echo "db_path=${DB_PATH}"
