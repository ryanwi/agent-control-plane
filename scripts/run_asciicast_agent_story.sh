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
say_what "Create a continuous-loop support agent from the canonical example file."
say_why "This proves integration starts with ordinary Python code, not framework magic."
pause_step1

run_cmd cp "${ROOT_DIR}/examples/continuous_loop_governance.py" "${AGENT_FILE}"

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
run_cmd uv run python "${AGENT_FILE}" --db "${DB_PATH}" --max-cycles 2 --loop-sleep-seconds 0.3
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
