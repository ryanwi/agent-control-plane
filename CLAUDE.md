# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Test Commands

```bash
uv sync --extra dev          # Install dependencies
uv run pytest -q             # Run all tests
uv run pytest tests/test_approval_gate.py -q  # Run single test file
uv run pytest -k "test_name" # Run single test by name
uv run ruff check src tests  # Lint
uv run ruff format src tests # Format
uv run mypy src              # Type check
make check                   # Lint + typecheck + test (all at once)
```

## Architecture

This is an embeddable governance framework for autonomous agent runtimes. It separates **control plane** (policy, approvals, budgets, kill switches) from **data plane** (actual execution of side effects).

### Source layout: `src/agent_control_plane/`

- **`engine/`** — Core control-plane engines. Each is a standalone class that takes an `AsyncSession` (SQLAlchemy) for DB operations:
  - `policy_engine.py` — Risk classification via pluggable `RiskClassifier` protocol + action tier lookup
  - `router.py` — `ProposalRouter` produces deterministic `RoutingDecision` from policy engine output
  - `approval_gate.py` — Ticket lifecycle, scoped session approvals, expiry
  - `budget_tracker.py` — Atomic session-level cost/count enforcement
  - `concurrency.py` — Resource locks and cycle serialization per session
  - `kill_switch.py` — Emergency stop by session/system/budget scope
  - `event_store.py` — Monotonic per-session event persistence; fail-closed for `state_bearing=True`, buffered for telemetry
  - `session_manager.py` — Session + policy snapshot CRUD

- **`recovery/`** — Post-crash and timeout handlers:
  - `crash_recovery.py` — Releases stale active cycles
  - `timeout_escalation.py` — Detects stuck cycles and emits escalation events

- **`types/`** — Pydantic v2 DTOs and enums (public API surface)
- **`models/`** — `ModelRegistry` for lazy ORM resolution + SQLAlchemy mixins for host apps to compose with their own `Base`

### Key patterns

- **ModelRegistry**: Engines resolve ORM models at runtime via `ModelRegistry.get("ModelName")` rather than importing concrete models. Host apps register their models at startup. `get()` returns `Any` to satisfy mypy.
- **All DB operations use `AsyncSession`**: Engines don't own transactions — the caller manages `commit()`.
- **Public API is centralized in `__init__.py`**: All stable exports go through the top-level `__all__`. Import from `agent_control_plane` directly, not from submodules.

### Control-plane flow (proposal lifecycle)

1. `PolicyEngine.classify()` → risk level + action tier
2. `ProposalRouter.route()` → routing decision with reason
3. `ApprovalGate.check_session_scope()` / `.create_ticket()` → approval check
4. `BudgetTracker.check_budget()` + `.increment()` → budget reservation
5. `ConcurrencyGuard.acquire_cycle()` → resource lock
6. `KillSwitch` check → emergency stop evaluation
7. Execution (caller's data plane)
8. `EventStore.append()` → audit event

## Conventions

- Use conventional commits: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`
- Don't add domain-specific business logic into core engine modules
- `state_bearing=True` persistence errors must fail closed (raise), never swallow
- Don't bypass control engines for state transitions — always go through the engine API
- Ruff config: line-length 120, target Python 3.11, rule sets E/F/I/UP/B/SIM
- Mypy strict on `src/`
- Tests use `pytest-asyncio` with `asyncio_mode = "auto"`
