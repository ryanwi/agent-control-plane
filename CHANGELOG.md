# Changelog

## [Unreleased]

- No unreleased changes.

## [0.1.0] - 2026-03-06

### Added

- Initial public release of a production-oriented agent governance control plane.
- Core engines:
  - `PolicyEngine`
  - `ProposalRouter`
  - `ApprovalGate`
  - `BudgetTracker`
  - `ConcurrencyGuard`
  - `KillSwitch`
  - `SessionManager`
  - `EventStore`
- Recovery handlers:
  - `CrashRecovery`
  - `TimeoutEscalation`
- Repository-driven storage architecture:
  - `SessionRepository`, `EventRepository`, `ApprovalRepository`, `ProposalRepository`
  - `AsyncUnitOfWork`, `SyncUnitOfWork`
- SQLAlchemy backends:
  - `AsyncSqlAlchemyUnitOfWork` + async repos
  - `SyncSqlAlchemyUnitOfWork` + sync repos
- First-class sync API:
  - `SyncControlPlane`
- Model and typing surface:
  - Reference ORM exports in `agent_control_plane.models`
  - Typed DTOs/enums for proposals, sessions, approvals, policies, and frames
  - `ModelRegistry` + SQLAlchemy mixins for host model composition
- Examples:
  - `examples/quickstart.py`
  - `examples/quickstart_sync.py`
  - `examples/security_agent.py`

### Changed

- `SessionManager.create_policy()` returns a `UUID`.
- `PolicyEngine.classify_action_tier()` respects explicit `always_approve` and `auto_approve` lists.
- Policy/action flow now uses typed enums (`ActionName`) internally with exact matching.
- Unknown actions are fail-closed (`ActionName.UNKNOWN`) and classified as blocked.
- Public package exports include storage protocols/backends, reference models, and sync API.
- Documentation updated to reflect async/sync UnitOfWork integration patterns.

### Fixed

- SQLite JSON serialization behavior in quickstart policy persistence (`model_dump(mode="json")`).
- Approval scope persistence consistency (`scope_resource_ids` support in model mixins).
- Event buffering preserves routing metadata (`routing_decision`, `routing_reason`).
- Event sequence allocation error reporting improved for missing counters.
- Crash recovery exception handling narrowed to expected runtime/value failures.

### Removed

- Removed `examples/sync_adapter.py` thread-loop bridge from the release surface.
