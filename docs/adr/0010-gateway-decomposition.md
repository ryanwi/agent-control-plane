# ADR 0010: Gateway Decomposition — Focused Facades (v0.18)

## Status

Accepted

## Date

2026-06-01

## Supersedes

[ADR 0009](0009-integration-patterns.md) (partially — gateway objects replace the flat `ControlPlaneFacade` as the primary consumer-facing API)

## Superseded by

None

## Context

After ADR 0009 shipped `ResilientControlPlane` and `ControlPlaneSetup`, the underlying `ControlPlaneFacade` / `SyncControlPlane` remained wide god-classes with 30+ public methods. Consumers targeting different concerns (session lifecycle, approvals, budget) had to import and hold a single object with the full method surface regardless of what they actually needed.

The width violated the single-responsibility principle in a way that generated pylint `too-many-public-methods` warnings — which were being suppressed rather than fixed. The warnings are a symptom: wide classes are hard to review, test in isolation, and reason about.

## Decision

Decompose `ControlPlaneFacade` into focused gateway objects, each ≤ 11 public methods:

| Gateway | Responsibility |
|---|---|
| `SessionGateway` | Session lifecycle (create, start, complete, abort, get, list) |
| `ApprovalGateway` | Approval ticket lifecycle (check, grant, deny, pending, get) |
| `BudgetGateway` | Budget checks, increments, summaries |
| `AgenticGateway` | Proposal submission, routing, evaluator pipeline |

Resilient variants (`ResilientSessionGateway`, `ResilientApprovalGateway`, etc.) wrap each gateway with the same fail-open/fail-closed semantics from ADR 0009.

Async variants (`AsyncSessionGateway`, `AsyncApprovalGateway`, etc.) provide the same split for async runtimes.

`ControlPlaneFacade` and `SyncControlPlane` are retained as lower-level entry points for advanced consumers who need direct access or are migrating incrementally. They are no longer the recommended starting point.

### Recommended usage pattern

```python
from agent_control_plane import ControlPlaneSetup, GovernanceConfig

cp = ControlPlaneSetup(database_url=db_url, governance=GovernanceConfig(...)).build()
# cp is a namespace with focused gateway attributes:
session = cp.sessions.start_session(...)
approval = cp.approvals.check_approval(action_id, session_id)
within_budget = cp.budget.check_budget(session_id, cost)
```

`ControlPlaneSetup.build()` returns an object whose attributes are the gateway instances — consumers hold only the gateways they need rather than the full facade.

### Method-count enforcement

A parity-guard test (`tests/test_gateway_parity.py`) asserts ≤ 11 public methods per gateway class. This test fails at CI time if a future contributor widens a gateway beyond the limit, eliminating the need for pylint suppression comments.

## Consequences

- **No suppressed pylint warnings**: every class is genuinely ≤ 11 methods.
- **Focused consumers**: code that only manages sessions doesn't import or hold approval/budget methods.
- **`ControlPlaneFacade` still available**: no forced migration for existing consumers.
- **Async parity**: async gateway classes mirror the sync surface; the parity guard catches drift.
- **Additive only**: `ResilientControlPlane` and `ControlPlaneSetup` from ADR 0009 are unchanged.
