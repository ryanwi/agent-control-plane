# Identity Integration Guide

## Objective

Integrate `agent-control-plane` with strong identity attribution and fail-closed governance defaults.

## Boundary pattern

1. Authenticate request at app boundary (OIDC/JWT/service credential).
2. Authorize operation in host application policy layer.
3. Map principal -> normalized `agent_id`.
4. Pass `agent_id`, `correlation_id`, and `idempotency_key` into control-plane calls/events.

## Minimum integration contract

- Every governed proposal has:
  - `session_id`
  - `agent_id`
  - `resource_id`
  - `decision` (typed action)
- Every key event emission carries:
  - stable `correlation_id`
  - optional `idempotency_key` for retries

## Fail-closed defaults

- `UnknownAppEventPolicy.RAISE` for app-event mapping.
- Mark critical transitions as `state_bearing=True`.
- Deny unknown tools/actions by default in MCP gateway mappings.

## MCP gateway identity notes

- Resolve caller identity before constructing `ToolCallContext`.
- Set `agent_id` in tool-call context from authenticated principal mapping.
- Use deterministic tool-name -> action mapping (`ToolPolicyMap`).
- Treat unmapped tools as denied until explicitly mapped.

## Token governance identity mapping

Token budget enforcement and model access policy require `IdentityContext` populated from the authenticated caller:

1. Map authenticated principal to `IdentityContext` fields:
   - `user_id` — individual user or service account identity
   - `org_id` — organization/tenant boundary
   - `team_id` — team/department for cost attribution
2. Pass `IdentityContext` to `TokenBudgetTracker` and `ModelGovernor` calls.
3. Populate `ToolCallContext` identity fields when using the MCP gateway:
   - `identity_user_id`, `identity_org_id`, `identity_team_id`
4. Budget configs use subset matching: a config with only `org_id` set matches any user in that org. Design configs to match your identity hierarchy.

```python
from agent_control_plane import IdentityContext, UserId, OrgId, TeamId

# Map from your auth layer
identity = IdentityContext(
    user_id=UserId(authn_principal.user_id),
    org_id=OrgId(authn_principal.org_id),
    team_id=TeamId(authn_principal.team_id),
)
```

## Delegation and handoff trust

Delegation (`DelegationGuard.propose_delegation`) and handoff (`request_handoff`) are
**advisory/audit records — they do not elevate trust**. An agent's effective authority is
always its own registered capabilities, resolved through `AgentMetadata.is_capable`; a target
agent never inherits the source's capabilities by being delegated to or handed off work.

This matters in multi-agent systems: do not treat a sub-agent's output (or a delegation/
handoff record) as higher-trust than any other input. Authorize every action against the
acting agent's own capabilities, and screen sub-agent output the same way you screen any tool
result. Wiring a handoff's `allowed_actions` into authorization would re-introduce the
trust-escalation vector this invariant exists to prevent.

## Quick checklist

- [ ] Authn at edge implemented and validated.
- [ ] Delegation/handoff treated as audit only — authorization uses the agent's own capabilities (`AgentMetadata.is_capable`), never inherited from a source agent.
- [ ] Authz at edge implemented for governed operations.
- [ ] `agent_id` propagation implemented.
- [ ] Correlation/idempotency propagation implemented.
- [ ] Unknown event/tool defaults set to fail-closed.
- [ ] Critical events marked state-bearing.
- [ ] `IdentityContext` populated from authenticated principal for token governance.
- [ ] Token budget configs scoped to appropriate identity level (user/org/team).
- [ ] Model access policy checked before routing (`ModelGovernor.check_access()`).
