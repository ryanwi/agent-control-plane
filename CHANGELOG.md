# Changelog

## [Unreleased]

- No unreleased changes.

## [0.2.0] - 2026-03-06

### Added

- Repository-driven storage architecture with async/sync protocol boundaries:
  - `SessionRepository`, `EventRepository`, `ApprovalRepository`, `ProposalRepository`
  - `AsyncUnitOfWork` and `SyncUnitOfWork`
- First-class SQLAlchemy backends:
  - `AsyncSqlAlchemyUnitOfWork` and async repository implementations
  - `SyncSqlAlchemyUnitOfWork` and sync repository implementations
- Reference ORM exports from `agent_control_plane.models` for easier host integration.
- `SyncControlPlane` facade (`src/agent_control_plane/sync.py`) for synchronous hosts.
- `examples/quickstart_sync.py` demonstrating native sync usage without an event-loop bridge.
- `examples/security_agent.py` end-to-end governance example for autonomous security actions.

### Changed

- Engine/recovery wiring now composes through repositories and unit-of-work integration.
- `SessionManager.create_policy()` now returns a `UUID` directly.
- `PolicyEngine.classify_action_tier()` now honors explicit `always_approve` and
  `auto_approve` action lists before defaulting.
- Public package exports now include storage backends/protocols, reference models, and sync API.
- README integration guidance updated for async/sync UnitOfWork patterns.

### Removed

- Removed legacy `examples/sync_adapter.py` thread-loop bridge example.

## [0.1.2] - 2026-03-05

### Added

- `examples/sync_adapter.py`: sync wrapper for calling the async control plane from synchronous code. Demonstrates `SyncControlPlane` class with background event loop, own SQLite database, and sync methods for `BudgetTracker` and `KillSwitch`.

## [0.1.1] - 2026-03-05

### Added

- Focused unit tests for core engines (38 new tests, 42 total):
  - `PolicyEngine` / `ProposalRouter`: 19 tests covering risk classification, action tier dispatch, asset scope filtering, case-insensitive blocking, dry_run vs live mode, and custom classifier protocol.
  - `BudgetTracker`: 10 tests covering cost/count boundary math, exact boundary pass/fail, zero-cost edge cases, remaining balance calculation, and session-not-found errors.
  - `KillSwitch`: 9 tests covering input validation, session abort with event emission, budget halt with correct reason/event, system halt across multiple sessions, and agent abort pause/cycle-clear behavior.

### Fixed

- `quickstart.py`: use `model_dump(mode="json")` to avoid `Decimal` serialization crash with SQLite JSON columns.
- `ApprovalTicketMixin`: added missing `scope_resource_ids` column.
- `PolicyEngine`: moved inline `Decimal` import to module level.
- `EventStore`: buffer now captures `routing_decision` and `routing_reason`; `_allocate_seq` raises a descriptive error instead of generic `NoResultFound`.
- `CrashRecovery`: narrowed broad `except Exception` to `(RuntimeError, ValueError)`.

### Changed

- Removed unused `risk_level` parameter from `ApprovalGate.check_session_scope()`.
- Clarified `KillSwitch._abort_agent` docstring regarding session scope behavior.

## [0.1.0] - 2026-03-05

### Added

- Initial standalone release of a production-oriented **agent governance control plane**.
- Core control plane engines:
  - `PolicyEngine` for risk tiering and policy evaluation.
  - `ProposalRouter` for deterministic routing and auditable routing decisions.
  - `ApprovalGate` for ticket lifecycle, scoped approvals, and expiry handling.
  - `BudgetTracker` with atomic session budget enforcement.
- `ConcurrencyGuard` to serialize overlapping work per session/resource.
  - `KillSwitch` for session/system/budget emergency stop semantics.
  - `SessionManager` for session lifecycle and policy snapshot persistence.
  - `EventStore` with per-session monotonic sequence numbering, fail-closed semantics for state-bearing writes, and buffering for non-state-bearing telemetry writes.
- Recovery handlers:
  - `CrashRecovery` for active-cycle recovery after process interruption.
  - `TimeoutEscalation` for stuck-cycle detection and escalation.
- Integration primitives:
  - `ModelRegistry` and SQLAlchemy mixins for host-application model composition.
  - Typed DTOs and enums for approvals, proposals, sessions, events, and policy definitions.
- Policy and classification features:
  - Pluggable `RiskClassifier` protocol and `DefaultRiskClassifier`.
  - Generic proposal scoring fields (`weight`, `score`) and resource-scoped approvals (`resource_id`, `scope_resource_ids`).
  - Generic execution parameters via `ExecutionIntentDTO.parameters`.
- Developer and adoption assets:
  - `examples/quickstart.py` with a runnable SQLite control-flow walkthrough.
- Test coverage:
  - Added regression tests for approval scope behavior, timeout escalation, and event buffering/failure handling.

### Changed

- Control-plane behavior is documented in terms of explicit governance versus execution separation.
- Failure semantics were clarified and aligned across engine boundaries for safer failure handling.
- Public docs and architecture references were expanded for external integration use.
- Public terminology and API names are domain-agnostic for multi-domain agent workloads.

### Documentation

- Added `README.md` quickstart and architecture framing for autonomous-agent control-plane usage.
- Added `docs/architecture.md` with:
  - Component map.
  - Control-plane lifecycle sequence.
  - Public API matrix.
  - Release-readiness checklist.
  - Operational gotchas and anti-patterns.

### Known Limitations

- This release intentionally excludes execution-plane implementations (tool callers, action adapters, and service connectors); those are expected to be supplied by host applications.
