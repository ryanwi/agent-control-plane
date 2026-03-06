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

- **`engine/`**: Core governance engines (e.g., `PolicyEngine`, `ApprovalGate`, `BudgetTracker`).
- **`recovery/`**: Post-crash and timeout handlers (`CrashRecovery`, `TimeoutEscalation`).
- **`types/`**: Public API surface consisting of Pydantic DTOs and Enums.
- **`models/`**: SQLAlchemy mixins and a `ModelRegistry` for lazy ORM resolution by host applications.

### Key Design Patterns
- **ModelRegistry:** Engines resolve ORM models at runtime via `ModelRegistry.get("ModelName")`. Host applications must register their concrete models at startup.
- **Async Execution:** All database operations utilize SQLAlchemy's `AsyncSession`. Engines do not manage transactions; the caller is responsible for `commit()`.
- **Centralized API:** The public API is strictly exported via `src/agent_control_plane/__init__.py`.

## Building, Running & Testing

Always use `uv run` to ensure the correct environment and dependencies are used.

### Setup & Sync
```bash
uv sync --extra dev          # Install all dependencies including dev tools
```

### Testing
```bash
uv run pytest -q             # Run all tests quietly
uv run pytest <path_to_test> # Run specific test file
make test                    # Alias for running tests
```

### Linting & Type Checking
```bash
uv run ruff check src tests  # Linting
uv run ruff format src tests # Formatting
uv run mypy src              # Strict type checking on source
make check                   # Run linting, type checking, and tests in sequence
```

## Development Conventions

### Coding Style
- **Python Version:** Target Python 3.11+.
- **Formatting:** Adhere to Ruff's default configuration (120 character line length).
- **Type Safety:** Maintain strict typing in `src/`. Use `Any` only where necessary for `ModelRegistry` compatibility.

### Best Practices
- **Fail Closed:** `state_bearing=True` persistence errors MUST raise an exception and fail the operation. Never swallow these errors.
- **Engine Integrity:** Never bypass control engines (e.g., `SessionManager`, `ApprovalGate`) to modify state directly via ORM models.
- **Auditability:** Every meaningful state transition must emit a corresponding event via the `EventStore`.
- **Conventional Commits:** Use standard prefixes: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`.

### Implementation Mandates
- **Surgical Changes:** Focus on the requested task. Avoid unrelated refactoring.
- **Test-Driven:** Always verify bug fixes with a reproduction test case. Ensure new features are covered by tests in the `tests/` directory.
- **Public API:** If adding new functionality, ensure it is properly exported in `src/agent_control_plane/__init__.py`.
