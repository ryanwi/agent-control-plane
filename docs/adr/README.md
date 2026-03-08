# ADR Index

Architecture Decision Records (ADRs) capture why important design choices were made, not just what changed.

- [0001: Public API Boundary](0001-public-api-boundary.md): defines stable vs internal import boundaries.
- [0002: Pre-1.0 Compatibility and Migration Notes](0002-pre-1-0-compatibility-and-migrations.md): defines breaking-change communication rules.
- [0003: Facade vs Builder Responsibility](0003-facade-vs-builder-responsibility.md): keeps extension wiring at composition boundaries.
- [0004: Idempotency Model for Mutating Operations](0004-idempotency-model.md): defines command-id replay guarantees.
- [0005: State-Bearing Event Semantics](0005-state-bearing-event-semantics.md): defines fail-closed durability behavior.
- [0006: Projection Feed vs Canonical Reads](0006-projection-vs-canonical-reads.md): defines when projection is warranted.
- [0007: Experimental Capability Contracts Are Informational Only](0007-experimental-capabilities-informational-only.md): defines non-enforcement intent for capability detection.

## How to write/update ADRs

- Filename format: `NNNN-short-kebab-title.md` (for example `0008-new-decision.md`).
- Required headers: `Status`, `Date`, `Supersedes`, `Superseded by`, `Context`, `Decision`, `Consequences`.
- Use status values: `Proposed`, `Accepted`, or `Superseded`.
- Link related decisions in a `Related ADRs` section.
- If behavior/contract changes, link relevant ADR IDs in `CHANGELOG.md` migration/release notes.
