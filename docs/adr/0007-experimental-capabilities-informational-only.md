# ADR 0007: Experimental Capability Contracts Are Informational Only

## Status

Accepted

## Date

2026-03-08

## Supersedes

None

## Superseded by

None

## Context

We need a way for host applications and companion integrations to detect optional runtime/deployment capabilities without coupling core governance behavior to paid or external feature logic.

## Decision

- Define capability contracts under `agent_control_plane.experimental.capabilities`.
- Wire capability providers at composition boundaries (currently builder helpers).
- Treat capability descriptors as **informational only**.
- Do not use capability detection as an enforcement gate in core governance paths.

## Consequences

- Core remains neutral and reusable across OSS and extended deployments.
- Integrators can discover runtime features without forking core.
- Pre-1.0 flexibility is preserved; experimental contracts may change in minor releases.

## Guardrails

- No entitlement or commercial logic in core engine/facade decision paths.
- Any move from informational detection to enforcement must be a separate ADR and explicit API contract update.

## Related ADRs

- [0003: Facade vs Builder Responsibility](0003-facade-vs-builder-responsibility.md)
- [0001: Public API Boundary](0001-public-api-boundary.md)
