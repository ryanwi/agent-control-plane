# Operations Runbook

## Goal

Provide a minimal, actionable workflow for operating `agent-control-plane` in production.

## Daily checks

- Review active sessions and stuck-cycle indicators.
- Review pending approvals and timeout aging.
- Check recent budget denials/exhaustion events.
- Confirm kill switch is not unintentionally active.

## Incident: runaway or unsafe execution

1. Trigger scoped kill switch:
- Session-level when isolated.
- Agent/system scope when blast radius is unclear.

2. Capture context:
- Session ID(s)
- Correlation IDs
- Last N events from replay

3. Stabilize:
- Deny/expire pending approvals as needed.
- Pause affected session/agent flows.

4. Investigate:
- Replay event timeline.
- Verify policy snapshot and routing reasons.
- Verify budget/approval history and tool/action attribution.

5. Recover:
- Resume or recreate session with corrected policy as appropriate.

## Incident: approval backlog

- Triage by risk/age/session priority.
- Deny stale or no-longer-valid requests.
- Confirm timeout escalation behavior is active.

## Recommended audit logging fields

- `session_id`
- `event_seq`
- `event_kind`
- `agent_id`
- `correlation_id`
- `idempotency_key`
- policy/routing reason
- budget deltas and remaining headroom

## Weekly controls review

- Validate approval scope constraints (resource, count, expiry).
- Validate budget thresholds against observed usage.
- Validate kill-switch procedures via tabletop or dry-run.
- Review unresolved or repeated denial reasons for policy tuning.
