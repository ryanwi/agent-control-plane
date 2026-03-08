# ADR 0004: Idempotency Model for Mutating Operations

## Status

Accepted

## Date

2026-03-08

## Supersedes

None

## Superseded by

None

## Context

Host applications retry operations due to network/process failures. Without deterministic idempotency, retries can duplicate state transitions.

## Decision

- Mutating facade operations accept optional `command_id`.
- `command_id` is recorded in a command ledger with operation name and result payload.
- Reuse of the same `command_id` for a different operation is rejected.
- Reuse of the same `command_id` for the same operation returns cached result.

## Consequences

- Retries are safer and deterministic for both human-run and agent-run workflows.
- Clients can implement at-least-once delivery with reduced duplicate side effects.
- Clear operation naming becomes part of contract safety.

## Guardrails

- New mutating facade operations should define and document idempotency behavior.
- Tests should cover: first execution, repeated same command, and conflicting operation reuse.

## Related ADRs

- [0002: Pre-1.0 Compatibility and Migration Notes](0002-pre-1-0-compatibility-and-migrations.md)
- [0005: State-Bearing Event Semantics](0005-state-bearing-event-semantics.md)
