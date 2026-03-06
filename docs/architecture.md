# Agent Control Plane Architecture

## 1) Positioning: control plane for autonomous agents

This package separates *decision governance* from *execution*.

It is intentionally designed as a reusable control-plane building block for:

- agent harnesses that coordinate multiple LLM/tool loops,
- workflows with human-in-the-loop approvals,
- systems needing policy/risk gating before side effects.

It is intended to be embedded into application runtimes (not replace the execution framework itself).

Typical production fits:

- Agent teams that need explicit governance for autonomous support, operations, or incident-response agents.
- Multi-agent research and analysis pipelines requiring routing, scoped approvals, and recovery semantics.
- CI/CD and infrastructure automation where policy/risk checks and kill-switches are mandatory.
- Workflow systems that need auditable, resumable execution decisions (not just prompt chaining).

- **Control plane**: classify proposals, enforce policy and budgets, route to agents, arbitrate approvals, and persist authoritative events.
- **Execution plane**: carry out side effects (agent actions, tool calls, service writes, notifications, etc.).

That mirrors standard networking/control-plane patterns:
- the control plane is authoritative policy and orchestration state.
- the data plane only executes what the control plane permits.

## 2) Package architecture

The package is organized around explicit layers:

- `engine/`
  - `policy_engine` — risk scoring and tiering
  - `router` — deterministic routing and audit-ready routing decisions
  - `approval_gate` — ticket lifecycle, scope handling, and timeout handling
  - `budget_tracker` — atomic session budget checks and increments
  - `session_manager` — session lifecycle and snapshots
  - `concurrency` — lock/serialize overlapping work paths
  - `kill_switch` — emergency stop semantics by scope
  - `event_store` — monotonic event persistence and buffering
- `recovery/`
  - `crash_recovery` — resume control state after process interruption
  - `timeout_escalation` — escalate stuck active cycles
- `types/` and `models/`
  - DTOs, enums, and SQLAlchemy mixins for host-system integration.

## 3) Control-plane lifecycle

Use this as the reference flow for new handlers.

```mermaid
sequenceDiagram
  autonumber
  participant App as App Service
  participant SP as SessionManager
  participant PE as PolicyEngine
  participant RT as ProposalRouter
  participant AG as ApprovalGate
  participant BT as BudgetTracker
  participant CG as ConcurrencyGuard
  participant KS as KillSwitch
  participant ES as EventStore
  participant EP as Execution Plane
  participant CR as Crash/Timeout Recovery

  App->>SP: create session + policy snapshot
  App->>PE: classify proposal intent
  PE->>RT: classify risk + route
  RT-->>App: routing decision + reason
  App->>AG: check scoped approvals
  AG-->>App: ticket/None
  App->>BT: reserve budget
  BT-->>App: budget check result
  App->>CG: acquire resource/session lock
  CG-->>App: lock token
  App->>KS: evaluate kill-switch scope
  KS-->>App: allowed / denied
  App->>EP: execute if allowed
  EP-->>ES: emit outcome events
  ES->>CR: replay/recover input for postmortem
```

## 4) Key guarantees

- **Deterministic routing**: policy and router decisions should be pure and reproducible with current policy snapshot.
- **Auditable**: every meaningful control transition creates an event.
- **Fail-safety**:
  - state-bearing writes fail closed.
  - telemetry events can be buffered when persistence is temporarily unavailable.
- **Recovery-ready**: crash and timeout pathways can release stale locks and continue gracefully.
- **Human override paths**: approvals and kill switches remain explicit, configurable, and logged.

## 5) Integration contracts

1. Register model classes with `ModelRegistry` at startup.
2. Keep all control-plane writes inside host-managed DB transactions.
3. Ensure session lifecycle is the source of truth for active cycle and status.
4. Route long-running work through one control-plane entrypoint per proposal.
5. Drive restart behavior through recovery runners before normal operation resumes.

## 5b) Persistence coupling and abstraction roadmap

Current design assumptions (v0.1):

- Transactions and row-locking semantics are required for correctness in:
  - active-cycle lock transitions,
  - budget increment checks,
  - session-scoped scope consumption,
  - and event sequence allocation/replay.
- `ModelRegistry` + `Session`-centric APIs are the concrete integration boundary for this release.

Proposed v0.2 decoupling path:

1. Extract narrow storage protocols (session/proposal/approval/event interfaces).
2. Move SQLAlchemy-specific model code into an adapter package (`adapters/sqlalchemy`).
3. Provide optional non-SQL backends using an optimistic-increment strategy where row locking is unavailable.
4. Keep durable audit semantics mandatory, even when implementation changes.

This means the package remains usable for any domain; only the storage runtime is abstracted by adapter.

## 6) Suggested extension points

- Replace asset policy checks with a custom classifier while keeping proposal fields unchanged.
- Add new `ActionTier` and `RiskLevel` mappings as your domain adds higher granularity risk controls.
- Customize approval scope semantics (resource/region/project/team) using existing scoped ticket fields.

## 7) Open-source framing

Most agent orchestration libraries offer coordination primitives.
This package is narrower and production-oriented:
- approval/risk/budget orchestration
- kill-switch escalation
- event-sourced recovery

Use it where correctness and operational safety matter as much as throughput.

The intended fit is:

- **High-confidence, low-latency demo agents:** optional and often overkill.
- **Production orchestration runtimes:** recommended; this package becomes the governance rail between intention and side effects.

## 8) Public API surface (stable exports)

Exports are centralized through [agent_control_plane/__init__.py](../src/agent_control_plane/__init__.py). Use that as the canonical import surface.

| Module | Public symbols | Stability contract |
| --- | --- | --- |
| `agent_control_plane` | `PolicyEngine`, `ProposalRouter`, `ApprovalGate`, `BudgetTracker`, `ConcurrencyGuard`, `KillSwitch`, `EventStore`, `SessionManager`, `CrashRecovery`, `TimeoutEscalation`, `ModelRegistry`, `RiskClassifier`, `DefaultRiskClassifier` | Core control-plane entry points for orchestration and recovery. |
| `agent_control_plane` | `ActionTier`, `RiskLevel`, `ApprovalStatus`, `ApprovalDecisionType`, `ProposalStatus`, `SessionStatus`, `EventKind`, `ExecutionMode`, `AbortReason`, `KillSwitchScope` | Enumerations used by all engines; considered stable between minor releases. |
| `agent_control_plane` | `ActionProposalDTO`, `SessionCreate`, `SessionSummary`, `PolicySnapshotDTO`, `ApprovalScopeDTO`, `ApprovalTicketDTO`, `RequestFrame`, `EventFrame`, `ResponseFrame` | DTOs are semantically stable; add optional fields in minor releases only. |
| `agent_control_plane.models` | `ModelRegistry`, `ControlSessionMixin`, `ControlEventMixin`, `ApprovalTicketMixin`, `PolicySnapshotMixin` | Intended for embedding into host SQLAlchemy models and runtime bootstrapping. |
| Private internals (non-API) | `engine.*`, `recovery.*`, `types.*`, `models.*` modules | Import by direct module path only when needed; avoid for long-term compatibility. |

## 9) v0.1 packaging / release checklist

Recommended pre-release validation:

1. Documentation complete:
   - `README.md` updated and installation flow verified.
   - Architecture reference current.
   - Public APIs documented by module.
2. Runtime bootstrap validated:
   - Model registry registration and startup wiring tested.
   - Recovery checks run at process start.
3. Safety defaults verified:
   - state-bearing failures fail closed.
   - bounded buffering configured and observed.
4. Test baseline:
   - Core control-plane tests pass.
   - At least one integration-style test for ticket → budget → kill-switch path.
5. Packaging ready:
   - `pyproject.toml` version bumped.
   - `README`, license, and classifiers aligned with audience.
6. Publish checklist:
   - Validate `uv`/pip install from sdist and wheel.
   - Validate import path from installed package.

## 10) Operational gotchas and anti-patterns

- Avoid calling model methods directly and bypassing engines; that breaks audit trails and recovery assumptions.
- Avoid sharing a single active cycle across multiple proposal streams without concurrency checks.
- Avoid unbounded scoped approvals (countless session scope without expiry) unless intentionally audited.
- Avoid swallowing `state_bearing=True` persistence errors; those failures must block the decision path.
- Avoid creating/using `EventKind` strings outside enum values.
- Avoid mutating policy snapshot data after session start; policies are designed as immutable execution anchors.
