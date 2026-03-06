# Gemini Project Context: agent-control-plane

This file provides foundational context and mandates for Gemini CLI when working in this repository.

## Project Overview

`agent-control-plane` is an embeddable governance framework for autonomous agent runtimes. It establishes a clear separation between the **control plane** (decision governance) and the **data plane** (execution of side effects).

### Core Mission
- **Deterministic Governance:** Enforce policies before agent execution.
- **Risk Gating:** Provide human-in-the-loop and risk-based approval gates.
- **Resource Guardrails:** Manage budgets and concurrency locks.
- **Audit & Recovery:** Maintain a monotonic event store for auditing and crash recovery.

### Key Technologies
- **Language:** Python 3.11+
- **Database/ORM:** SQLAlchemy 2.0+ (Async)
- **Validation:** Pydantic 2.0+
- **Tooling:** `uv` (package management), `ruff` (linting/formatting), `mypy` (type checking), `pytest` (testing).

## Architecture & Components

The source code is located in `src/agent_control_plane/` and organized into logical layers:

- **`engine/`**: Core governance engines. Each engine is a standalone class that takes an `AsyncSession` for DB operations.
  - `policy_engine.py`: Risk classification via pluggable `RiskClassifier` protocols.
  - `router.py`: Produces deterministic `RoutingDecision` from policy engine output.
  - `approval_gate.py`: Ticket lifecycle, scoped session approvals, and expiry.
  - `budget_tracker.py`: Atomic session-level cost/count enforcement.
  - `concurrency.py`: Resource locks and cycle serialization per session.
  - `kill_switch.py`: Emergency stop by session, system, or budget scope.
  - `event_store.py`: Monotonic per-session event persistence (fail-closed for `state_bearing=True`).
  - `session_manager.py`: Session and policy snapshot CRUD.
- **`mcp/`**: Model Context Protocol (MCP) gateway implementation for standardized tool access.
- **`storage/`**: Persistence protocols and concrete SQLAlchemy implementations (Async and Sync).
- **`recovery/`**: Post-crash and timeout handlers (`CrashRecovery`, `TimeoutEscalation`).
- **`types/`**: Public API surface consisting of Pydantic DTOs and Enums.
- **`models/`**: SQLAlchemy mixins and a `ModelRegistry` for lazy ORM resolution by host applications.

### Key Design Patterns
- **ModelRegistry:** Engines resolve ORM models at runtime via `ModelRegistry.get("ModelName")`. Host applications must register their concrete models at startup.
- **Async Execution:** All database operations utilize SQLAlchemy's `AsyncSession`. Engines do not manage transactions; the caller is responsible for `commit()`.
- **Centralized API:** The public API is strictly exported via `src/agent_control_plane/__init__.py`.

## Reference Examples

- **Core Engine:** `src/agent_control_plane/engine/approval_gate.py` (Pattern for engine implementation).
- **Storage Protocol:** `src/agent_control_plane/storage/protocols.py` (Pattern for defining persistence interfaces).
- **Testing:** `tests/test_approval_gate.py` (Pattern for testing engines with fakes and async sessions).

## Development Commands

Always use `uv run` to ensure the correct environment and dependencies are used.

- **Setup & Sync:** `uv sync --extra dev`
- **Testing:** `uv run pytest -q` (or `make test`)
- **Linting:** `uv run ruff check src tests`
- **Formatting:** `uv run ruff format src tests`
- **Type Checking:** `uv run mypy src`
- **Full Check:** `make check` (Lint + Type Check + Test)

## Verification Checklist

Before considering a task complete, ensure the following steps are performed:

1. [ ] **Linting:** Run `uv run ruff check src tests` and fix all issues.
2. [ ] **Formatting:** Run `uv run ruff format src tests`.
3. [ ] **Type Checking:** Run `uv run mypy src` and ensure it passes strictly.
4. [ ] **Tests:** Run `uv run pytest -q` and ensure all tests pass.
5. [ ] **New Tests:** Add a new test case to verify any bug fixes or new features.

## Implementation Mandates

### Best Practices
- **Fail Closed:** `state_bearing=True` persistence errors MUST raise an exception and fail the operation. Never swallow these errors.
- **Engine Integrity:** Never bypass control engines (e.g., `SessionManager`, `ApprovalGate`) to modify state directly via ORM models.
- **Auditability:** Every meaningful state transition must emit a corresponding event via the `EventStore`.
- **Conventional Commits:** Use standard prefixes: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`.

### Technical Standards
- **Surgical Changes:** Focus on the requested task. Avoid unrelated refactoring.
- **Test-Driven:** Always verify bug fixes with a reproduction test case.
- **Public API:** If adding new functionality, ensure it is properly exported in `src/agent_control_plane/__init__.py`.
- **Documentation:** Refer to `docs/` for deep dives on architecture, security, and operations.
