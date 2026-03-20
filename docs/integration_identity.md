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

## Quick checklist

- [ ] Authn at edge implemented and validated.
- [ ] Authz at edge implemented for governed operations.
- [ ] `agent_id` propagation implemented.
- [ ] Correlation/idempotency propagation implemented.
- [ ] Unknown event/tool defaults set to fail-closed.
- [ ] Critical events marked state-bearing.
- [ ] `IdentityContext` populated from authenticated principal for token governance.
- [ ] Token budget configs scoped to appropriate identity level (user/org/team).
- [ ] Model access policy checked before routing (`ModelGovernor.check_access()`).
