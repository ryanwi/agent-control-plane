# ADR 0008: Token Governance Engines Are Optional Composable Steps

## Status

Accepted

## Date

2026-03-19

## Supersedes

None

## Superseded by

None

## Context

Companies tracking per-employee AI token usage need identity-scoped, time-windowed budget enforcement and model access policy. The control plane already had session-scoped budgets via `BudgetTracker`, but lacked per-user/org/team token governance.

Two design approaches were considered:

1. **Wire into ProposalRouter**: automatically run token budget checks and model access checks inside the existing routing pipeline.
2. **Optional composable steps**: provide standalone engines that host apps invoke at well-defined points in the proposal lifecycle, without modifying ProposalRouter internals.

## Decision

- `TokenBudgetTracker` and `ModelGovernor` are **optional composable engines**, not wired into `ProposalRouter` or `SyncControlPlane` internals.
- Host apps invoke them at specific lifecycle points:
  - `ModelGovernor.check_access()` — sync, before routing (step 2)
  - `TokenBudgetTracker.check_budget()` — async, before execution (step 5)
  - `TokenBudgetTracker.record_usage()` — async, after execution (step 8)
- This follows the precedent set by `SessionRiskAccumulator` (v0.10.0), which is also caller-invoked and not wired into the router.

## Consequences

- **No breaking changes**: existing proposal lifecycle is untouched.
- **Opt-in adoption**: teams can adopt token governance incrementally without changing existing integration code.
- **Host apps control composition**: different deployments can choose which governance steps to apply and in what order.
- **Testing is independent**: each engine is testable in isolation with in-memory fakes.
- **Trade-off**: host apps must remember to call the engines — there is no automatic enforcement. This is acceptable because:
  - The control plane is an embedded library, not a hosted service.
  - Host apps already manage the proposal lifecycle explicitly.
  - Automatic wiring would force all deployments to pay the cost of token governance even when not needed.

## Guardrails

- Do not add token governance calls inside `ProposalRouter.route()` or `SyncControlPlane` without a new ADR.
- If automatic enforcement is later desired, provide an opt-in middleware or decorator pattern rather than modifying core routing.

## Related ADRs

- [0001: Public API Boundary](0001-public-api-boundary.md)
- [0003: Facade vs Builder Responsibility](0003-facade-vs-builder-responsibility.md)
