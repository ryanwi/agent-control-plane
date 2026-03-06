# AGENTS.md — agent-control-plane

## Purpose

This repository is the standalone `agent-control-plane` package:

- A governance/control layer for autonomous-agent runtimes.
- Embedded/self-hosted library (not a hosted control-plane SaaS).
- Not a trading product, not a data-plane service, and not tied to any host domain schema.
- Focused on policy, approvals, budgets, kill switches, event persistence, and recovery.

## Scope and boundaries

- Engine modules (`src/agent_control_plane/engine/*`) contain execution logic.
- Recovery modules (`src/agent_control_plane/recovery/*`) handle crash/timeout handling.
- Types (`src/agent_control_plane/types/*`) define public DTOs and enums.
- Model utilities (`src/agent_control_plane/models/*`) are integration helpers only.
- Tests live in `tests/*` and should validate behavior at both success and failure paths.

## Development setup

```bash
uv sync --extra dev
make check
```

## Developer quickstart

Use the `Makefile` targets so commands always run in the project-managed `uv` environment:

```bash
make sync
make test
make lint
make format
make typecheck
make check
```

Equivalent direct commands:

```bash
uv sync --extra dev
uv run pytest -q
uv run ruff check src tests
uv run ruff format src tests
uv run mypy src
```

## Required workflow for edits

- Prefer minimal, targeted changes.
- Preserve API/behavior in public exports under `src/agent_control_plane/__init__.py` unless the change is intentional and versioned.
- For intentional API changes, update tests, README, and `CHANGELOG.md` in the same change.
- Add/adjust tests for any behavior changes, especially:
  - approval/risk/budget/guidance paths,
  - kill-switch and recovery paths,
  - event persistence and buffering semantics.

## Task routing (progressive disclosure)

- API/export changes: check `src/agent_control_plane/__init__.py`, `README.md`, and `CHANGELOG.md`.
- Identity/security integration changes: check `docs/integration_identity.md` and `docs/security_model.md`.
- Operational/recovery behavior changes: check `docs/operations_runbook.md` and `docs/architecture.md`.
- MCP gateway changes: check `src/agent_control_plane/mcp/*` and MCP sections in `README.md`.

## Key runtime contracts

- **ModelRegistry**: host applications must register required models at startup.
- **EventStore semantics**:
  - `state_bearing=True` errors should fail closed (raise).
  - non-state-bearing failures should not make critical path fail; capture/propagate via buffer behavior.
- **Session control**: do not bypass control engines for state transitions.
- **Recovery safety**: keep deterministic lock and status transitions.

## Commit conventions (for contributors)

Use conventional commits:

- `feat: ...`
- `fix: ...`
- `test: ...`
- `docs: ...`
- `refactor: ...`
- `chore: ...`

## Release hygiene

Before publishing:

- Keep docs in `README.md` and `docs/architecture.md` consistent.
- Ensure `CHANGELOG.md` contains the release notes.
- Run the test suite.
- Confirm no uncommitted files remain.

## Anti-patterns

- Don’t add domain-specific business logic (trading, infra, exchange-specific assumptions) into core engine modules.
- Don’t call internal/private ORM operations from outside the `ModelRegistry` boundary.
- Don’t introduce global mutable state in engine constructors.
- Don’t widen `state_bearing` behavior without explicit reasoning.

## Documentation

- Main references:
  - `README.md`
  - `docs/architecture.md`
  - `docs/integration_identity.md`
  - `docs/security_model.md`
  - `docs/operations_runbook.md`
  - `CHANGELOG.md`
