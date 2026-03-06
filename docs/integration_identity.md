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

## Quick checklist

- [ ] Authn at edge implemented and validated.
- [ ] Authz at edge implemented for governed operations.
- [ ] `agent_id` propagation implemented.
- [ ] Correlation/idempotency propagation implemented.
- [ ] Unknown event/tool defaults set to fail-closed.
- [ ] Critical events marked state-bearing.
