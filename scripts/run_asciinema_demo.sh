#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_PATH="${1:-${ROOT_DIR}/control_plane_asciinema_demo.db}"

if ! command -v sqlite3 >/dev/null 2>&1; then
  echo "sqlite3 is required for DB inspection output" >&2
  exit 1
fi

echo "==> Running sync control-plane demo"
uv run python "${ROOT_DIR}/examples/asciinema_sync_demo.py" --db "${DB_PATH}"

echo
echo "==> SQLite tables"
sqlite3 "${DB_PATH}" '.tables'

echo
echo "==> Sessions"
sqlite3 "${DB_PATH}" <<'SQL'
.headers on
.mode column
SELECT id, session_name, status, max_cost, used_cost, max_action_count, used_action_count
FROM control_sessions;
SQL

echo
echo "==> Proposals"
sqlite3 "${DB_PATH}" <<'SQL'
.headers on
.mode column
SELECT id, session_id, resource_id, decision, status, action_tier, risk_level
FROM action_proposals
ORDER BY created_at;
SQL

echo
echo "==> Approval tickets"
sqlite3 "${DB_PATH}" <<'SQL'
.headers on
.mode column
SELECT id, session_id, proposal_id, status, decision_type, decided_by
FROM approval_tickets
ORDER BY created_at;
SQL

echo
echo "==> Control events"
sqlite3 "${DB_PATH}" <<'SQL'
.headers on
.mode column
SELECT session_id, seq, event_kind, state_bearing, agent_id
FROM control_events
ORDER BY seq;
SQL

echo
echo "==> Command ledger"
sqlite3 "${DB_PATH}" <<'SQL'
.headers on
.mode column
SELECT command_id, operation, session_id
FROM command_ledger
ORDER BY created_at;
SQL
