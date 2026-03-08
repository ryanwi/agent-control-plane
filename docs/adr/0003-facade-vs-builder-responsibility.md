# ADR 0003: Facade vs Builder Responsibility

## Status

Accepted

## Date

2026-03-08

## Supersedes

None

## Superseded by

None

## Context

New extension surfaces (for example capability detection) can be added in many places. Overloading facades early increases API sprawl.

## Decision

- Keep governance facades focused on control-plane behavior and state transitions.
- Introduce composition-time extension wiring at builder/service assembly boundaries first.
- Keep capability detection informational only and non-authoritative in core.

## Consequences

- Facade APIs stay smaller and easier to stabilize later.
- Integrations can evolve through composition without widening governance surfaces.
- If a composition extension matures, it can be promoted later with deliberate API design.

## Guardrails

- Do not add capability/entitlement checks to governance decision paths without a dedicated ADR.
- Prefer optional constructor wiring in composition helpers before adding new facade parameters.

## Related ADRs

- [0001: Public API Boundary](0001-public-api-boundary.md)
- [0007: Experimental Capability Contracts Are Informational Only](0007-experimental-capabilities-informational-only.md)
