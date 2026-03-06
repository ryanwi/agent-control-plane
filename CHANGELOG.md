# Changelog

## [Unreleased]

- No unreleased changes.

## [0.5.0] - 2026-03-06

### Added

- `SessionLifecycleResult` DTO for sync lifecycle operations.
- `SyncControlPlane.complete_session()` and `SyncControlPlane.abort_session()` now return `SessionLifecycleResult`.
- `ControlPlaneFacade.close_session()` and `ControlPlaneFacade.abort_session()` now return `SessionLifecycleResult`.

### Changed

- `emit_app_event(...)` and `ControlPlaneFacade.emit_app(...)` now accept optional overrides for:
  - `state_bearing`
  - `agent_id`
  - `correlation_id`
  - `idempotency_key`
- README now highlights importing `UnknownAppEventPolicy` from `agent_control_plane.types`.

## [0.4.0] - 2026-03-06

### Added

- New embedded MCP gateway module: `agent_control_plane.mcp`.
- `McpGateway` for governed MCP tool-call execution.
- Typed MCP gateway interfaces:
  - `ToolCallContext`
  - `ToolCallResult`
  - `ToolExecutor`
  - `ToolPolicyMap`
  - `McpGatewayConfig`
  - `McpEventMapper`
- Typed MCP event enum: `McpEventName`.
- Typed MCP governance errors:
  - `McpGovernanceError`
  - `PolicyDeniedError`
  - `ApprovalRequiredError`
  - `BudgetDeniedError`
  - `KillSwitchActiveError`
  - `ToolExecutionError`
- MCP gateway example: `examples/mcp_tool_gateway.py`.
- MCP gateway tests: `tests/test_mcp_gateway.py`.

### Changed

- `SyncControlPlane` now exposes:
  - `session_scope()` for extension flows that need a raw session context.
  - `get_session(session_id)` for typed session-state reads.
- `ControlPlaneFacade` now exposes `get_session(session_id)`.
- README expanded with MCP tool-call gateway integration guidance.

## [0.3.1] - 2026-03-06

### Changed

- **Comparable RiskLevel**: Refactored `RiskLevel` to be a comparable enum with numeric ranking. This enables safer, more readable threshold checks (e.g., `risk_level <= max_risk_tier`).
- **Simplified Policy Engine**: Removed fragile list-index based risk comparisons in favor of direct enum comparison operators.

### Added

- New unit tests for risk level comparison logic in `tests/test_enums.py`.

## [0.3.0] - 2026-03-06

### Added

- First-class sync event APIs on `SyncControlPlane`:
  - `emit_event(...)`
  - `replay_events(...)`
  - `emit_app_event(...)`
- Typed app-event mapping surface:
  - `AppEventMapper` protocol
  - `DictEventMapper` registry mapper
  - `MappedEventDTO`
  - `UnknownAppEventError`
  - `UnknownAppEventPolicy`
- New `ControlPlaneFacade` high-level sync API for host application integration.
- New tests for sync event APIs, mapper behavior, and facade lifecycle flow.

### Changed

- Sync APIs now support direct app-event-to-control-event mapping without thread-loop adapters.
- Public API exports updated for sync facade and app-event mapping primitives.
- README sync integration docs expanded with event/facade guidance.

## [0.2.1] - 2026-03-06

### Added

- New finite-domain enums for stronger API typing:
  - `RoutingResolutionStep`
  - `AssetMatch`
  - `AgentScope`
- `KillResultDTO` for typed sync kill-switch responses.

### Changed

- Router decisions now use typed `RoutingResolutionStep` instead of raw string step names.
- Asset classifier contract now returns `AssetMatch` instead of string values.
- Event paths now use typed `EventKind` through:
  - `EventStore`
  - storage protocols
  - async/sync SQLAlchemy repositories
  - `EventFrame`
- Session/proposal repository interfaces now use enum types for status/mode fields:
  - `SessionStatus` for session filtering
  - `ProposalStatus` for proposal status updates
  - `ExecutionMode` in session creation paths (`SessionManager`, `SyncControlPlane`)
- Sync API `kill()` / `kill_all()` now return `KillResultDTO` (typed scope and counters).

### Fixed

- Removed remaining string comparisons in examples and policy/router tests where enum comparisons are now authoritative.

## [0.2.0] - 2026-03-06

### Added

- **Agent Registry**: Central engine for managing registered agent identities, versions, and tags.
- **Capability Governance**: Formalized agent capabilities (e.g., "I can isolate pods") mapped to allowed actions.
- **Governed Delegation**: New `DelegationGuard` for secure task hand-off between agents with full audit trail.
- **Polymorphic Routing**: Refactored `PolicyEngine` to use `ActionPolicyHandler` pattern for cleaner, extensible routing logic.
- **Audit Viewer**: New `examples/audit_viewer.py` utility for replaying and formatting session event timelines.
- **Identity-Validated Routing**: `ProposalRouter` now optionally validates that proposing agents are registered and authorized for specific actions.
- **Advanced Stress-Test Examples**:
  - `examples/zombie_agent.py`: Validates crash recovery.
  - `examples/ghosted_agent.py`: Validates timeout escalation.
  - `examples/panic_agent.py`: Validates global kill switch behavior.
  - `examples/compliance_agent.py`: Validates regex-based asset scoping.
  - `examples/rate_limited_agent.py`: Validates concurrency and budget limits.
  - `examples/multi_agent_delegation.py`: Validates identity-linked delegation flows.

### Changed

- **Enum Migration**: Comprehensive refactor to use typed enums (`ActionName`, `RiskLevel`, `EventKind`, `ExecutionMode`) across all engines, DTOs, and repositories.
- **Improved Type Safety**: Resolved all `mypy` strict type-checking issues across the entire `src/` directory.
- **Kill Switch Metadata**: `KillSwitch` now returns `KillSwitchScope` enum values in metadata dictionaries for better consistency.
- **Timeout Refactor**: `TimeoutEscalation` logic consolidated into `ApprovalGate.expire_timed_out_tickets()`.

### Fixed

- **SQL Timeout Reliability**: Replaced Python-side UTC comparisons with SQL `func.now()` for robust multi-timezone ticket expiration.
- **Crash Recovery Constructor**: Added missing `event_repo` argument to `CrashRecovery` initialization.
- **Session Policy Consistency**: Fixed `SessionManager.create_policy()` to return `UUID` directly, resolving SQLAlchemy persistence crashes.
- **Policy Prioritization**: Improved `PolicyEngine` to correctly prioritize explicit `always_approve`/`auto_approve` lists over generic risk levels.
- **Case-Insensitivity**: Policy list matching is now case-insensitive for better usability.

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
