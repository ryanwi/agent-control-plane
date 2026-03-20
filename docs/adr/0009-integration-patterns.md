# ADR 0009: Integration Patterns — Resilient Facade and Configuration Builder

## Status

Accepted

## Date

2026-03-19

## Supersedes

None

## Superseded by

None

## Context

Multiple real consumers independently built the same patterns on top of `ControlPlaneFacade`:

1. **Fail-open/fail-closed wrappers** — try/except around every CP call with logging and mode-dependent behavior (30–40% of each wrapper).
2. **Multi-step bootstrap ceremony** — alias profiles, action names, risk patterns, model governance, event mapping, all configured separately before the facade is usable (~100 lines each).
3. **Convenience methods** — domain-friendly names wrapping raw facade calls.

When independent consumers converge on the same wrapper pattern, those patterns belong in the library. The control plane positions itself as an **in-process, embeddable governance framework** — analogous to Casbin for authorization or OPA for policy — not a remote service. Integration ceremony must be minimal for this positioning to hold.

### The wrapper facade anti-pattern

Each consumer built a class that:
- Holds a `ControlPlaneFacade` instance
- Wraps every method with try/except
- Logs warnings on failure
- Returns `None` or a safe default for non-critical operations
- Raises on state-bearing operations (session transitions, budget increments)

This is a **wrapper facade** — a layer that exists only because the underlying API doesn't provide the right failure semantics. It adds no domain logic, only resilience policy. When every consumer needs the same wrapper, the wrapper belongs in the library.

## Decision

### 1. `ResilientControlPlane`

A wrapper around `ControlPlaneFacade` that adds configurable fail-open/fail-closed semantics per operation category:

- **`STATE_BEARING`** (session transitions, budget increments) — fail-closed by default
- **`TELEMETRY`** (event emission, scorecard export) — fail-open by default
- **`QUERY`** (reads, health checks, replays) — fail-open by default
- **`BUDGET`** (budget checks, not increments) — fail-open by default (returns `True`, allowing execution when budget check fails)

Three resilience modes:
- `FAIL_OPEN` — all operations return safe defaults on error
- `FAIL_CLOSED` — all operations raise on error
- `MIXED` (default) — state-bearing operations fail-closed, everything else fail-open

Consumers can override the mode per category for fine-grained control.

### 2. `ControlPlaneSetup`

A single configuration object that replaces the multi-step bootstrap ceremony:

```python
cp = ControlPlaneSetup(
    database_url=db_url,
    alias_profile=MY_ALIASES,
    risk_patterns=MY_RISK_PATTERNS,
    model_governance=MY_MODEL_POLICY,
    resilience_mode=ResilienceMode.MIXED,
).build()
```

This replaces ~100 lines of bootstrap code in each consumer with ~10 lines. The builder handles: table creation, model registration, alias profile application, action name registration, event mapper configuration, risk pattern setup, model governance setup, and resilient facade wrapping.

### 3. Positioning: in-process, not remote

The control plane is an **in-process library**, not a remote service. This is a deliberate architectural choice:

- **Latency**: governance checks on the critical path must be sub-millisecond
- **Availability**: the control plane cannot be a single point of failure for agent execution
- **Simplicity**: no network calls, no serialization, no service discovery

A future `RemoteControlPlane` client (ADR TBD) will provide the same interface over the network for teams that need centralized policy, but the in-process model is the primary deployment posture.

## Consequences

- **LOC reduction**: consumers replace 200–400 lines of wrapper code with ~10 lines of configuration
- **Consistent failure semantics**: all consumers get the same resilience behavior without independent reimplementation
- **No breaking changes**: `ControlPlaneFacade` and `SyncControlPlane` remain unchanged; `ResilientControlPlane` and `ControlPlaneSetup` are additive
- **Onboarding tiers**: new consumers start with `ControlPlaneSetup` → `ResilientControlPlane`; advanced consumers drop to `ControlPlaneFacade` or individual engines as needed
- **Future direction**: `ControlPlaneSetup` becomes the natural place to wire in new composable engines (inference router, resource policies) as they're added
