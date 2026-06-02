# Security Model

## Purpose

This document defines the security posture of `agent-control-plane` as an embedded governance layer.

## Trust boundaries

- **App boundary (outside control plane):** caller authentication and authorization.
- **Control plane boundary (inside app runtime):** policy/risk/approval/budget/kill-switch decisions.
- **Execution boundary:** side-effecting tools/services called only after governance decisions.

## Protected assets

- Session state and lifecycle transitions.
- Approval tickets and scope constraints.
- Budget counters and thresholds.
- Event log integrity and replayability.
- Agent identity attribution in decisions/events.

## Threat scenarios and controls

1. Unknown or unregistered operation/tool invocation
- Control: fail-closed mapping (`ActionName.UNKNOWN`, `UnknownAppEventPolicy.RAISE`), policy denial path.
  `ControlPlaneSetup` defaults `EventConfig.unknown_event_policy` to `RAISE`; unmapped `emit_app`
  calls raise `UnknownAppEventError` regardless of the resilience mode
  (`UnknownAppEventError` is re-raised before the generic TELEMETRY fail-open handler).

2. Budget abuse/runaway execution
- Control: single conditional `UPDATE … WHERE used_cost + cost ≤ max_cost …` makes
  `increment_budget` (sync backend) atomic — no TOCTOU between check and write.
  `BUDGET` operations in MIXED resilience mode are `FAIL_CLOSED`; a DB error on
  `check_budget` raises rather than silently allowing spend.
  `TokenUsage.estimated_cost_usd` is validated non-negative at construction.

3. Unauthorized high-risk action
- Control: policy tiering + approval gate for manual review.

4. Runaway or compromised runtime
- Control: scoped kill switch (`session`, `agent`, `system`, `budget` semantics).
  Agent-abort (`KillSwitchScope.AGENT_ABORT`) emits a state-bearing `KILL_SWITCH_TRIGGERED`
  event so the record survives a DB outage.

5. Lost auditability during failures
- Control: state-bearing events fail closed; non-state-bearing telemetry may buffer.

11. Malicious or poisoned tool output (prompt injection / exfiltration)
- Threat: a trusted tool returns attacker-controlled content (e.g. a poisoned README, an
  injected instruction, or an exfiltration URL) that would re-enter the agent's context.
  Pre-execution policy cannot catch this — the request looked benign; the danger is in the
  response.
- Control: optional `ResponseEvaluator`s run on `McpGateway` *after* execution and *before*
  output is returned. Any deny fails closed — `ToolResultRejectedError` is raised (carrying
  only the evaluator name), the payload is withheld from the caller, and a state-bearing
  `TOOL_RESULT_REJECTED` event records the evaluator name and a fixed sanitized category. The
  evaluator's free-text reason may echo screened output, so it is never persisted to the
  event store nor surfaced in the caller error; it is written only to the local operator log.
  The tool's cost is still charged: the external call genuinely ran. The built-in
  `RegexResponseEvaluator` screens for injection/exfil markers and non-allowlisted outbound
  URLs; hosts can plug a small, fast LLM-backed classifier behind the same protocol.

12. Over-broad egress allowlist (capability vs destination)
- Threat: an allowlist entry is treated as "this destination is safe," so every operation
  reachable at an allowlisted destination becomes permitted. Allowing an API host for one
  operation implicitly allows every other operation reachable there — e.g. permitting a host
  for messages also permits file uploads to that host under an attacker-supplied account.
- Control: model egress as a *capability grant*, not a destination filter. `EgressEvaluator`
  maps each destination to the specific capabilities granted there and fails closed on both
  an unknown destination and a granted destination invoked with an ungranted capability.
  Note that `DefaultAssetClassifier` is only a coarse destination filter (substring match on
  the resource id); it answers "is this asset in scope," not "which operation is permitted."

13. Corrupt or tampered persisted session state on resume/startup
- Threat: session state (budget counters, status, abort metadata) is persisted and reloaded
  every time a session is resumed, activated, or crash-recovered. A DB rollback, corruption,
  or tampering could feed invalid state back into a running session.
- Control: `validate_session_integrity()` runs before every transition that trusts persisted
  state — `SessionManager.activate_session`/`resume_session`, the async facade equivalents,
  and the crash-recovery startup sweep (including non-stuck ACTIVE sessions). Violations fail
  closed (raise) and emit a state-bearing `SESSION_STATE_INVALID` audit event.
- Limitation: the invariants are *structural* (non-negative counters, `ABORTED ⟹ abort_reason`).
  They catch gross corruption but **not** an attacker who *lowers* `used_cost`/`used_action_count`
  to regain budget — those values stay non-negative and pass. Sound detection requires
  reconciling the persisted counter against an independent, complete ledger of cost increments
  (each `increment_budget` would need to emit a cost-bearing event); no such ledger exists for
  session budgets today, so reconciliation is a documented follow-up rather than an active check.

## Zero Trust integration guidance

- Authenticate every caller at the app edge (OIDC/JWT/service credentials).
- Authorize every action before constructing control-plane proposals.
- Propagate principal identity to `agent_id` and correlation metadata.
- Prefer explicit deny/fail-closed defaults for unknown events/tools.

## Token governance trust boundaries

Token budget enforcement and model access policy introduce additional trust considerations:

6. Identity spoofing for budget bypass
- Control: `IdentityContext` (user/org/team) must be populated from authenticated principal at the app boundary, never from untrusted client input. Budget configs match on identity fields — a spoofed `user_id` could consume another user's budget or bypass restrictions.

7. Budget config tampering
- Control: `TokenBudgetConfig` creation should be restricted to admin/operator roles. Budget configs are persisted via `AsyncTokenBudgetRepository` — protect write paths with authorization checks at the host boundary.

8. Model access policy bypass
- Control: `ModelGovernor.check_access()` is a sync pre-routing check. Host apps must invoke it before routing; the control plane does not auto-enforce it. Skipping the check bypasses model tier restrictions.

9. Cost attribution integrity
- Control: `TokenUsage.estimated_cost_usd` is caller-provided and validated non-negative
  at construction (`model_validator` raises `ValidationError` for negative values). Host
  apps should compute cost from authoritative LLM billing data, not from client-reported
  values. Inaccurate or manipulated cost reporting undermines budget enforcement.

10. Cross-identity budget leakage
- Control: identity matching uses subset semantics (an org-level config matches any user in that org). Ensure budget configs are scoped appropriately — an overly broad config (e.g., only `org_id` set) applies to all users in that org.

14. Malicious evaluator plugin overrides built-in
- Threat: a package installed in the same environment registers a plugin under the `regex`
  entry-point name (or any built-in name), replacing the built-in evaluator with one that
  always returns `allow=True`.
- Control: `EvaluatorRegistry._discover` now logs a warning and skips any plugin whose
  name collides with an already-registered evaluator. Built-ins registered via `register()`
  before `_discover` runs are protected. For complete isolation, pass `auto_discover=False`
  and register only explicitly trusted evaluators.

## Out of scope

- Identity provider management (OIDC provider, key rotation, SSO lifecycle).
- Network perimeter controls, secret management platforms, endpoint protection.
- Hosted control-plane operations.
