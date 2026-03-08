# ADR 0001: Public API Boundary

## Status

Accepted

## Date

2026-03-08

## Supersedes

None

## Superseded by

None

## Context

The repository is evolving quickly and exposes many modules. Contributors and downstream apps need a shared rule for what is safe to depend on.

## Decision

- Treat package-root exports (`agent_control_plane.__init__`) as the primary public API.
- Treat `agent_control_plane.experimental.*` as explicitly non-stable.
- Treat direct imports from internal modules (`engine.*`, `recovery.*`, `types.*`, `models.*`) as implementation detail unless re-exported at package root.

## Consequences

- Humans have a clear dependency boundary when reviewing changes.
- Agents can classify edits as API-facing vs internal with less ambiguity.
- Internal refactors stay easier while root exports remain the integration contract.

## Guardrails

- Any change to package-root exports requires changelog entry and migration notes if breaking.
- New symbols should be added to root only when intended for broad downstream use.

## Related ADRs

- [0002: Pre-1.0 Compatibility and Migration Notes](0002-pre-1-0-compatibility-and-migrations.md)
- [0003: Facade vs Builder Responsibility](0003-facade-vs-builder-responsibility.md)
