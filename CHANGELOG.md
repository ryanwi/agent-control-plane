# Changelog

## [Unreleased]

## [0.9.2] - 2026-03-08

### Fixed

- Package version metadata now matches release tag (`0.9.2`) for package managers and runtime version checks (`importlib.metadata`, `agent_control_plane.__version__`, `agent_control_plane.get_version()`).

## [0.9.1] - 2026-03-08

### Changed

- Added root package version helpers (`__version__`, `get_version()`) and documented naming conventions.
- Superseded by `v0.9.2` for package metadata correctness (`v0.9.1` tag still reported version `0.9.0`).

### Breaking (pre-1.0)

- Removed `*DTO` suffixes from public type names in `agent_control_plane.types` and facade signatures.
- Public model exports were renamed to `*Row` to avoid collisions with suffix-free domain type names:
  - `ActionProposalRow`
  - `ApprovalTicketRow`
  - `PolicySnapshotRow`
- Query and scorecard types were renamed to suffix-free forms (for example: `Page`, `SessionHealth`, `StateChangePage`, `ControlPlaneScorecard`).

### Added

- New first-class proposal creation APIs:
  - `ControlPlaneFacade.create_proposal(proposal, command_id=...)`
  - `AsyncControlPlaneFacade.create_proposal(proposal, command_id=...)`
- Proposal repository protocol/storage support for `create_proposal(...)` in sync and async SQLAlchemy backends.
- New deterministic helper for proposal command IDs:
  - `proposal_command_id(session_id, resource_id, resource_type, decision, ...)`
- Experimental capability contracts in `agent_control_plane.experimental.capabilities`:
  - `CapabilityProvider`, `CapabilitySet`, `CapabilityDescriptor`
  - `ControlPlaneCapability`, `StaticCapabilityProvider`, mapping helpers
- Builder composition helpers now accept optional `capability_provider` and expose `get_capabilities()` on service bundles.
- New compatibility reference: `docs/compatibility.md`.
- Package version helpers at root export surface:
  - `__version__`
  - `get_version()`

### Changed

- Documentation now includes an explicit dev/prod DB guide (SQLite vs Postgres), reliability checklist, and expanded operations runbook for deployment and incident response.
- Architecture/README now describe capability detection as a composition-time extension point (informational only, non-authoritative for governance enforcement).
- Naming docs now standardize role-based conventions (domain/contract types, `*Row` persistence classes, no `DTO` suffixes).
- ADR guidance is now documented in `docs/adr/README.md` for decision context and change rationale.
- Release notes for behavior/public-contract changes should include relevant ADR links.

## [0.7.0] - 2026-03-08

### Added

- Deterministic benchmark primitives for agentic experimentation:
  - `BenchmarkScenarioSpec`, `BenchmarkRunSpec`, `BenchmarkRunResultDTO`, `FitnessWeights`
  - `run_benchmark(...)`, `run_batch(...)`, `hash_config(...)`, `WeightedFitnessEvaluator`
- Pluggable policy interfaces for evaluator and guardrail decisions:
  - `EvaluatorPolicy`, `GuardrailPolicy`
  - `ThresholdEvaluatorPolicy`, `PassThroughGuardrailPolicy`
- Telemetry export helpers for events and scorecards:
  - `export_event(...)`, `export_scorecard(...)`
  - `TracerLike`, `MeterLike` protocol contracts
- Richer operational scorecards (sync + async) including:
  - guardrail allow counts and policy-code histograms
  - evaluation block-reason histograms
  - budget denied/exhausted counters
  - approval and checkpoint->rollback latency percentiles
  - average cost per successful action and handoff acceptance rate

### Changed

- Public exports now include benchmark DTOs/utilities, policy interfaces, and telemetry helpers.

## [0.6.0] - 2026-03-08

### Added

- New agentic-governance DTOs and exports:
  - checkpoints/rollback (`SessionCheckpointDTO`, `RollbackResultDTO`)
  - goal/planning (`GoalDTO`, `PlanDTO`, `PlanStepDTO`, `PlanProgressDTO`)
  - evaluation/guardrails (`EvaluationResultDTO`, `GuardrailDecisionDTO`)
  - handoff/scorecard (`HandoffResultDTO`, `ControlPlaneScorecardDTO`)
- New enums for agentic workflows:
  - `GoalStatus`, `PlanStepStatus`, `EvaluationDecision`, `GuardrailPhase`
- New sync/async facade primitives:
  - `create_checkpoint`, `list_checkpoints`, `rollback_to_checkpoint`
  - `create_goal`, `create_plan`, `start_plan_step`, `complete_plan_step`, `get_plan_progress`
  - `record_evaluation`, `apply_guardrail`, `request_handoff`, `get_operational_scorecard`
- New tests validating core agentic primitive flows:
  - `tests/test_agentic_primitives.py`

## [0.5.3] - 2026-03-08

### Added

- Companion gateway runnable entrypoint at `examples/companion_gateway/main.py`.
- Companion gateway auth policy hooks:
  - `DenyAllAuthPolicy` (default)
  - `AllowAllAuthPolicy`
  - `BearerTokenAuthPolicy`
- Contract tests now validate OpenAPI request bodies and common error responses (`404`, `409`, `422`).

### Changed

- Companion gateway now returns standardized error envelopes (`ErrorResponse`) for HTTP and validation errors.
- OpenAPI contract now declares `401` and `422` responses for gateway endpoints.

### Fixed

- Package version metadata now matches the latest release tag (`0.5.3`) for package managers and `importlib.metadata`.

## [0.5.0] - 2026-03-08

### Added

- `ControlPlaneFacade` now supports sync approval write operations:
  - `create_ticket`
  - `approve_ticket`
  - `deny_ticket`
- README now includes a canonical state-feed projection consumer loop using `get_state_change_feed(...)`.
- Sync facade tests now cover:
  - approval write flow behavior and command-id idempotency
  - end-to-end state-feed projection convergence against canonical reads
  - alias helper usage in a boundary payload workflow
- Added canonical HTTP API contract at `docs/openapi/control-plane-v1.yml` for companion gateway services and dashboards.
- Added companion gateway starter at `examples/companion_gateway` with:
  - REST endpoints mapped to facade/query operations
  - minimal embedded dashboard endpoint (`/dashboard`)
- Added OpenAPI contract response tests for the companion gateway against `docs/openapi/control-plane-v1.yml`.

### Changed

- README now points to the OpenAPI contract and clarifies the companion-service pattern for API/UI deployment.
- CI now validates OpenAPI specs via `make openapi-check`.

## [0.3.2] - 2026-03-08

### Added

- Public alias utility helpers in `agent_control_plane.types`:
  - `apply_inbound_aliases(data, profile)`
  - `apply_outbound_aliases(data, profile)`
- Package root exports now include alias utility helpers for non-DTO mapping workflows.

### Changed

- `RiskLimits.validate_extension()` now fails fast when no extension schema is registered, matching `extension_as()` semantics.

## [0.3.1] - 2026-03-08

### Fixed

- Resolved mypy failures in alias key normalization and profiled dump typing.
- Resolved MCP gateway action typing mismatch for `ActionValue` vs enum `.value` access.

## [0.2.0] - 2026-03-07

### Added

- New query/feed types in `agent_control_plane.types.query`:
  - `PageDTO`
  - `StateChangeDTO`
  - `StateChangePageDTO`
  - `SessionHealthDTO`
  - `CommandResultDTO`
- New control-plane command ledger model:
  - `CommandLedgerMixin`
  - `CommandLedger` reference model (`command_ledger` table)
- New storage protocol surfaces:
  - Proposal reads: `get_proposal`, `list_proposals`
  - Ticket reads: `get_ticket` (sync), `list_tickets`
  - Event feed reads: `list_state_bearing_events`
  - Command idempotency repository: `get_command`, `record_command`
- New SQLAlchemy repository implementations:
  - `AsyncSqlAlchemyCommandRepo`
  - `SyncSqlAlchemyCommandRepo`
- New facade read APIs (async and sync):
  - `get_proposal`, `list_proposals`
  - `get_ticket`, `list_tickets`
  - `get_state_change_feed`
  - `get_health_snapshot`
- Async command-id idempotency support for key mutations:
  - `open_session`
  - `create_ticket`
  - `approve_ticket`
  - `deny_ticket`

### Changed

- `AsyncSqlAlchemyUnitOfWork` and `SyncSqlAlchemyUnitOfWork` now expose `command_repo`.
- Public exports updated in package root, `storage`, `models`, and `types` modules for new query/feed/idempotency symbols.
- Async facade test suite expanded to cover:
  - proposal/ticket read APIs
  - state-change feed behavior
  - health snapshot
  - command-id idempotency on repeated calls

## [0.1.9] - 2026-03-07

### Added

- Added `AsyncControlPlaneFacade.get_ticket(ticket_id)` to fetch a single approval ticket by ID regardless of status.
- Added async approval repository `get_ticket(ticket_id)` support in protocols and SQLAlchemy async storage.
- Added async facade tests covering:
  - existing ticket lookup by ID
  - missing ticket lookup returning `None`
  - lookups across `PENDING`, `APPROVED`, `DENIED`, and `EXPIRED` statuses

## [0.1.8] - 2026-03-06

### Added

- Expanded `AsyncControlPlaneFacade` operations so async hosts can avoid direct repository/UoW usage for common flows:
  - Session lifecycle: `list_sessions`, `activate_session`, `pause_session`, `resume_session`, `set_active_cycle`
  - Concurrency helpers: `acquire_cycle`, `release_cycle`
  - Approvals: `create_ticket`, `approve_ticket`, `deny_ticket`, `get_pending_tickets`, `expire_timed_out_tickets`
  - Policy creation: `create_policy`
  - Recovery helpers: `recover_stuck_sessions`, `check_stuck_cycles`
- Typed ID aliases in `agent_control_plane.types.ids`:
  - `AgentId`
  - `ResourceId`
  - `IdempotencyKey`

### Changed

- Key public/storage signatures now accept typed ID aliases for stronger compile-time contracts.
- MCP and engine call sites were updated to convert boundary strings into typed ID aliases.

## [0.1.7] - 2026-03-06

### Changed

- `KillSwitch` now returns typed `KillSwitchResult` objects (replacing untyped dictionaries).
- `KillResultDTO.session_id` is now `UUID | None` instead of string.
- `RequestFrame.action` now uses typed `ActionName` values with fail-closed parsing.
- `SessionState.abort_reason` now uses `AbortReason | None`.
- `asset_scope` in session/policy DTOs now uses `AssetScope | None`.

### Added

- New enum: `AssetScope`.
- New typed DTO: `KillSwitchResult`.

## [0.1.6] - 2026-03-06

### Changed

- `ControlPlaneFacade.close_session()` now defaults to `final_event_kind=None` to avoid accidental double-emits.
- `ControlPlaneFacade.emit(...)` now accepts the same attribution/state-bearing options as low-level sync emit paths.
- `EventFrame` now includes `state_bearing`.
- SQLAlchemy async/sync event repositories now persist and hydrate `state_bearing` as a first-class field.

### Added

- `AsyncControlPlaneFacade` for async-native integration paths.
- `ScopedModelRegistry` and `registry_scope(...)` for instance-scoped registry isolation.
- Facade constructor injection for engine/session factory/UoW factory/registry in sync and async entrypoints.
- Lightweight builders:
  - `build_session_event_budget(...)`
  - `build_kill_switch_stack(...)`

## [0.1.5] - 2026-03-06

### Added

- New security and operations documentation:
  - `docs/security_model.md`
  - `docs/operations_runbook.md`
  - `docs/integration_identity.md`

### Changed

- README positioning now explicitly differentiates embedded/self-hosted usage from hosted control-plane platforms.
- README now includes an identity/zero-trust integration section and links to operator/security docs.
- Architecture docs now include:
  - embedded deployment posture
  - identity boundary guidance
  - control objectives
  - explicit non-goals

## [0.1.4] - 2026-03-06

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

### Additional scope included in 0.1.4


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

## [0.1.3] - 2026-03-06

### Changed

- **Comparable RiskLevel**: Refactored `RiskLevel` to be a comparable enum with numeric ranking. This enables safer, more readable threshold checks (e.g., `risk_level <= max_risk_tier`).
- **Simplified Policy Engine**: Removed fragile list-index based risk comparisons in favor of direct enum comparison operators.

### Added

- New unit tests for risk level comparison logic in `tests/test_enums.py`.

## [0.1.2] - 2026-03-06

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

## [0.1.1] - 2026-03-06

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

### Additional scope included in 0.1.1


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
