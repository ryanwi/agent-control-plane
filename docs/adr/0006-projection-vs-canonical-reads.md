# ADR 0006: Projection Feed vs Canonical Reads

## Status

Proposed

## Context

Some host applications need fast local read models; others can read canonical control-plane state directly. Teams need a default rule to avoid unnecessary complexity.

## Decision

- Default integration path: canonical reads from facade APIs (`get_*`, `list_*`).
- Use feed/cursor projection (`get_state_change_feed`) only when there is a clear performance, isolation, or workflow need.
- Projection consumers must checkpoint cursor progress and support replay-safe recovery.

## Consequences

- New adopters avoid premature complexity.
- Advanced adopters get a clear projection model when needed.
- Human and agent maintainers can reason about when cursor/checkpoint infrastructure is justified.

## Guardrails

- If projection is adopted, include parity checks against canonical reads.
- Projection capability should not replace canonical reads as the source of truth.

