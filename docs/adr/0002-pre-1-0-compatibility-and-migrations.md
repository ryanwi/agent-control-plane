# ADR 0002: Pre-1.0 Compatibility and Migration Notes

## Status

Accepted

## Date

2026-03-08

## Supersedes

None

## Superseded by

None

## Context

The project is pre-1.0 and shipping quickly. Breaking changes may be necessary, but downstream integrators need predictable communication.

## Decision

- Allow breaking changes in minor releases while pre-1.0.
- Disallow silent breaking changes.
- Require explicit migration notes in `CHANGELOG.md` for every breaking change.
- Keep compatibility posture documented in `docs/compatibility.md`.

## Consequences

- Maintainers keep iteration speed.
- Integrators (human and agent) can upgrade with an explicit migration path.
- Release quality depends on accurate changelog discipline.

## Guardrails

- PRs that alter public contracts must include migration notes.
- Docs that describe integration contracts (`README.md`, `docs/architecture.md`) must be updated in the same change.

## Related ADRs

- [0001: Public API Boundary](0001-public-api-boundary.md)
- [0004: Idempotency Model for Mutating Operations](0004-idempotency-model.md)
