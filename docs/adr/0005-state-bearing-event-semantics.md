# ADR 0005: State-Bearing Event Semantics

## Status

Proposed

## Context

Control-plane correctness depends on durable state transitions. Not all events are equally critical.

## Decision

- `state_bearing=True` events are authoritative state transitions and must fail closed on persistence failure.
- Non-state-bearing events are best-effort telemetry and may be buffered during failures.
- Recovery and replay logic should prioritize state-bearing streams as source-of-truth change history.

## Consequences

- Stronger correctness guarantees for session/proposal/ticket lifecycle transitions.
- Better separation between audit-critical events and operational telemetry.
- Integrators can design alerting/retry policy based on event criticality.

## Guardrails

- Never downgrade a state-bearing path to non-state-bearing without explicit rationale and tests.
- Tests should verify fail-closed behavior for state-bearing persistence failures.

