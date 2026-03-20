# Operations Runbook

## Goal

Provide an actionable baseline for operating `agent-control-plane` reliably in production.

## Deployment baseline

- DB posture:
  - Local/dev: SQLite is acceptable for single-process execution.
  - Production: use Postgres for concurrent workers, durability controls, and operational tooling.
- Startup order:
  1. Register models and initialize schema.
  2. Run crash recovery and timeout escalation scans.
  3. Start serving traffic only after recovery checks complete.
- Transaction posture:
  - Route all control-plane mutations through facade/engine calls inside a UoW.
  - Do not bypass engines with direct ORM writes for session/ticket/event state transitions.

## Daily checks

- Review active sessions and stuck-cycle indicators.
- Review pending approvals and timeout aging.
- Review budget denials and budget exhaustion events.
- Review token budget denials and model access denials per identity.
- Confirm kill switch is not unintentionally active.
- Review command-idempotency behavior for repeated operator actions.

## Weekly checks

- Validate approval scope constraints (resource, count, expiry).
- Validate budget thresholds against observed usage.
- Validate token budget configs per identity/org/team against observed token consumption.
- Validate kill-switch procedures via tabletop or dry-run.
- Review repeated denial reasons for policy tuning.
- Verify backups and restore drills for control-plane tables.

## Core telemetry and alerting

Minimum signals to monitor:

- session counts by status (`created`, `active`, `paused`, `aborted`, `completed`)
- pending approvals and age percentiles
- budget denials/exhaustions per time window
- token budget denials per identity/org/team
- model access denials by model tier
- kill-switch triggers by scope
- stuck-cycle recovery counts
- event append failures segmented by `state_bearing`

Recommended alerts:

- sustained growth in pending approvals
- spikes in budget exhaustion or kill-switch events
- any state-bearing persistence failures
- stuck active cycles beyond expected recovery window

## Incident: runaway or unsafe execution

1. Contain:
   - Trigger kill switch at smallest safe scope (session first, then agent/system if needed).
2. Capture:
   - Record affected session IDs, correlation IDs, event seq range, and operator command IDs.
3. Stabilize:
   - Deny or expire pending approvals for affected sessions.
   - Pause active sessions if further investigation is required.
4. Diagnose:
   - Replay events and verify policy snapshot, guardrail outcomes, and budget timeline.
5. Recover:
   - Resume or recreate sessions after policy/config correction and explicit operator validation.

## Incident: approval backlog

1. Triage by risk/age/session priority.
2. Deny stale or no-longer-valid requests.
3. Confirm timeout escalation is functioning.
4. Review policy thresholds and auto-approve conditions for obvious bottlenecks.

## Incident: DB outage or degradation

1. Enter safe mode:
   - Treat state-bearing write failures as hard failures.
   - Pause entrypoints that would create unsafe partial progress.
2. Verify blast radius:
   - Determine whether failures are isolated or systemic across workers.
3. Restore service:
   - Recover DB availability first, then run recovery checks before resuming normal traffic.
4. Post-incident:
   - Validate event sequence continuity and no orphaned active cycles.
   - Confirm operator actions are idempotent when retried.

## Recommended audit logging fields

- `session_id`
- `event_seq`
- `event_kind`
- `agent_id`
- `correlation_id`
- `idempotency_key`
- policy/routing reason
- budget deltas and remaining headroom
