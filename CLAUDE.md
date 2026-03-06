# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Test Commands

```bash
uv sync --extra dev          # Install dependencies
uv run pytest -q             # Run all tests
uv run pytest tests/<file> -q  # Run single test file
uv run pytest -k "test_name" # Run single test by name
make check                   # Lint + typecheck + test (all at once)
```

## Architecture

Embeddable governance framework for autonomous agent runtimes. Separates **control plane** (policy, approvals, budgets, kill switches) from **data plane** (actual execution of side effects). See `docs/architecture.md` for full design rationale.

### Source layout: `src/agent_control_plane/`

- **`engine/`** — Core engines (policy, routing, approvals, budgets, concurrency, kill switch, events, sessions, agent registry, action policy). Each is a standalone class; caller manages DB transactions.
- **`storage/`** — Repository protocol interfaces (`protocols.py`) + SQLAlchemy backends (async & sync). Decouples engines from any specific DB.
- **`mcp/`** — `McpGateway` for governing MCP tool calls through the control plane.
- **`sync.py`** — `SyncControlPlane` / `ControlPlaneFacade` — synchronous high-level API wrapping the async engines.
- **`recovery/`** — Crash recovery (stale cycle release) and timeout escalation.
- **`types/`** — Pydantic v2 DTOs and enums: `enums`, `policies`, `proposals`, `approvals`, `agents`, `frames`, `sessions`.
- **`models/`** — `ModelRegistry` for lazy ORM resolution, SQLAlchemy mixins, and ready-to-use reference models (`reference.py`).

### Key patterns

- **Repository protocols** in `storage/protocols.py` define the DB abstraction. Engines depend on protocol interfaces, not concrete backends.
- **ModelRegistry**: Host apps register ORM models at startup; engines resolve via `ModelRegistry.get("ModelName")`.
- **Engines don't own transactions** — the caller manages `commit()`.
- **Public API is centralized in `__init__.py`**: Import from `agent_control_plane` directly, not from submodules.

### Control-plane flow (proposal lifecycle)

1. `PolicyEngine.classify()` → risk level + action tier
2. `ProposalRouter.route()` → routing decision
3. `ApprovalGate` → session-scope check or ticket creation
4. `BudgetTracker` → budget check + increment
5. `ConcurrencyGuard` → resource lock
6. `KillSwitch` → emergency stop check
7. Execution (caller's data plane)
8. `EventStore.append()` → audit event

Higher-level entry points: `McpGateway` (MCP tool calls) and `SyncControlPlane` (synchronous facade) orchestrate this flow automatically.

## Conventions

- Use conventional commits: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`
- Don't add domain-specific business logic into core engine modules
- `state_bearing=True` persistence errors must fail closed (raise), never swallow
- Don't bypass control engines for state transitions — always go through the engine API
- Ruff and mypy config lives in `pyproject.toml` — run `make check` to verify
- Tests use `pytest-asyncio` with `asyncio_mode = "auto"`

## Further docs

- `docs/architecture.md` — Full architecture and deployment posture
- `docs/security_model.md` — Trust boundaries and security posture
- `docs/integration_identity.md` — Identity integration guide
- `docs/operations_runbook.md` — Production operations workflow
