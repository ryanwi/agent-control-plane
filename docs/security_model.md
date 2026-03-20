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

2. Budget abuse/runaway execution
- Control: pre-check + atomic budget increment and budget-deny handling.

3. Unauthorized high-risk action
- Control: policy tiering + approval gate for manual review.

4. Runaway or compromised runtime
- Control: scoped kill switch (`session`, `agent`, `system`, `budget` semantics).

5. Lost auditability during failures
- Control: state-bearing events fail closed; non-state-bearing telemetry may buffer.

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
- Control: `TokenUsage.estimated_cost_usd` is caller-provided. Host apps should compute cost from authoritative LLM billing data, not from client-reported values. Inaccurate cost reporting undermines budget enforcement.

10. Cross-identity budget leakage
- Control: identity matching uses subset semantics (an org-level config matches any user in that org). Ensure budget configs are scoped appropriately — an overly broad config (e.g., only `org_id` set) applies to all users in that org.

## Out of scope

- Identity provider management (OIDC provider, key rotation, SSO lifecycle).
- Network perimeter controls, secret management platforms, endpoint protection.
- Hosted control-plane operations.
