# Changelog

## [Unreleased]

### Changed

- **Breaking:** Generalized domain-specific naming to be domain-agnostic:
  - Renamed budget fields: `notional`/`max_notional`/`used_notional` → `cost`/`max_cost`/`used_cost` across DTOs, mixins, and engines.
  - Renamed proposal fields: `allocation_pct` → `weight`, `confidence` → `score`. Both are now optional (default `0`).
  - Removed `time_horizon`, `max_allocation`, `max_concentration_pct`, `max_single_allocation_pct` fields.
  - Renamed `ExecutionIntentDTO.quantity` → `parameters` (dict).
  - Removed `scope_symbols` and `security_id` backward-compat shims; use `resource_id` and `scope_resource_ids` only.
  - Removed `InstrumentLockedError` alias and `check_instrument_lock()`; use `ResourceLockedError` and `check_resource_lock()`.
  - Emptied `ActionTiers` defaults — users define their own tier lists.
- Added pluggable `RiskClassifier` protocol and `DefaultRiskClassifier` to `PolicyEngine`, replacing hardcoded allocation/confidence thresholds.
- Clarified persistence contract: v0.1 remains SQLAlchemy-first with durable, transaction-backed state.
  - Added architecture/readme notes and planned adapter path for future pluggable backends.

### Added

- `RiskClassifier` protocol for domain-specific risk classification.
- `DefaultRiskClassifier` using generic `weight`/`score` thresholds.
- `examples/quickstart.py` with a runnable, dependency-light SQLite walkthrough of a proposal through policy, approval, budget, concurrency, and event persistence flows.

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
- Test coverage:
  - Added regression tests for approval scope behavior, timeout escalation, and event buffering/failure handling.

### Changed

- Control-plane behavior is documented in terms of explicit governance versus execution separation.
- Failure semantics were clarified and aligned across engine boundaries for safer failure handling.
- Public docs and architecture references were expanded for external integration use.

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
