# CONTEXT.md — Agent Control Plane

Ubiquitous-language glossary. An agent reading this can reason about ACP without re-explanation in every prompt.

---

## What ACP Is

**Agent Control Plane (ACP)** — embeddable governance framework that enforces safety rules *before* agents execute side effects. It is the **control plane** (classify, route, approve, budget, audit); the host application is the **data plane** (actual execution). ACP does not replace orchestration libraries (LangChain, etc.) or IAM.

---

## The Proposal Flow

Every agent action becomes an **`ActionProposal`** and travels this deterministic pipeline:

```
PolicyEngine.classify()
  → ConditionEvaluator (auto-approve rules)
  → ProposalRouter.route()           → RoutingDecision
  → ParallelPolicyEvaluator          (concurrent custom checks)
  → ApprovalGate                     (ticket creation / check)
  → BudgetTracker                    (cost + count enforcement)
  → ConcurrencyGuard                 (resource lock)
  → KillSwitch                       (emergency stop check)
  → PreconditionVerifier             (pre-execution state check)
  → [caller executes in data plane]
  → EventStore.append()              (audit)
```

---

## Core Terms

### ActionProposal
Request by an agent to execute an action. Key fields:
- `decision` — the action name (e.g. `"delete_database"`)
- `agent_id`, `session_id`, `resource_id`, `resource_type`
- `action_tier`, `risk_level`, `status`
- `preconditions` — optional pre-execution state checks
- `reasoning` — agent's explanation

`status` lifecycle: `PENDING → APPROVED/DENIED → EXECUTED/FAILED` (or `EXPIRED`)

### ActionTier
How a proposal is routed after policy classification:
- `BLOCKED` — policy denies unconditionally
- `ALWAYS_APPROVE` — auto-approved, no ticket
- `AUTO_APPROVE` — approved if auto-approve conditions pass
- `STEER` — allowed but corrective guidance issued (see `SteeringContext`)
- `UNRESTRICTED` — no governance gates

### RiskLevel
Proposal risk: `LOW`, `MEDIUM`, `HIGH`. Comparable via `.rank`. Can be escalated mid-session by `SessionRiskAccumulator`.

### RoutingDecision
Output of `ProposalRouter`: contains `tier`, `risk_level`, `reason`, `resolution_step` (how the decision was reached), and optional `steering`.

### SteeringContext
When tier is `STEER`: `guidance` text + `suggested_actions` (alternatives) + `max_retries`.

---

## Sessions

### SessionState
Named execution boundary for a continuous agent run.
- `status`: `CREATED → ACTIVE → PAUSED/COMPLETED/ABORTED`
- `execution_mode`: `DRY_RUN` (safe, no side effects) | `LIVE` (real) | `REPLAY` (deterministic replay)
- `max_cost` / `used_cost` — Decimal USD budget
- `max_action_count` / `used_action_count`
- `active_policy_id` — immutable policy snapshot frozen at session start
- `killed_at` — set when kill switch triggered; blocks resume until cleared

Policy is **immutable once a session starts** — prevents mid-session policy changes breaking routing consistency.

### ExecutionMode
`DRY_RUN` (default, no real side effects) | `LIVE` | `REPLAY`

### AbortReason
Why a session ended: `OPERATOR_REQUEST`, `KILL_SWITCH`, `BUDGET_EXHAUSTED`, `AGENT_TIMEOUT`, `SYSTEM_ERROR`, `POLICY_VIOLATION`

---

## Policy

### PolicySnapshot
Governance rules frozen at session creation:
- `action_tiers` — maps action names → `ActionTier`
- `risk_limits` — score/weight thresholds
- `auto_approve_conditions` — `ConditionNode` tree
- `approval_timeout_seconds`
- `execution_mode`

### ConditionNode
Recursive boolean rule tree for auto-approval. Leaf types: `RiskLevelCondition`, `WeightCondition`, `ScoreCondition`, `ActionCondition`, `AssetCondition`, `EvaluatorCondition`. Composites: `AndCondition`, `OrCondition`, `NotCondition`. Max depth: 6.

---

## Approvals

### ApprovalTicket
Human-in-the-loop approval request.
- `status`: `PENDING → APPROVED/DENIED/EXPIRED/REVOKED`
- `decision_type`: `ALLOW_ONCE` | `ALLOW_FOR_SESSION`
- `scope`: constraints (resource_ids, max_cost, max_count, expiry)
- `timeout_at` — auto-denied if not manually approved
- Can be revoked post-approval

### ApprovalGate
Creates tickets and checks whether an existing ticket covers a proposal. Uses `FOR UPDATE` row lock to prevent approval races.

---

## Budgets

### BudgetTracker
Session-level cost + action-count enforcement. `reserve_budget()` pre-checks; `charge()` increments. Raises `BudgetExhaustedError`. **FAIL_CLOSED** — never swallow budget errors.

### TokenBudgetTracker
Identity-scoped, time-windowed LLM token/cost governance. Period: `DAILY/WEEKLY/MONTHLY/UNLIMITED`. Ledger row written *before* raising `TokenBudgetExhaustedError` — over-reporting spend is safer than under-reporting.

### IdentityContext
Who owns the token budget: `user_id`, `org_id`, `team_id`.

---

## Risk & Patterns

### SessionRiskAccumulator
Watches action history; escalates risk when accumulated score crosses thresholds or a `RiskPattern` matches. Sits between policy classification and routing.

### RiskPattern
Named ordered action sequence that triggers escalation: `action_sequence` (e.g. `["read_database", "send_email"]`), `window_size`, `escalate_to` (`RiskLevel`).

### RuntimeMonitor
Cooperative mid-execution interrupt — polls session risk while an action is in flight; signals `CancellableExecution` to stop if risk escalates. Non-coercive: ACP requests, caller decides. Emits state-bearing `RUNTIME_INTERRUPT_REQUESTED`.

---

## Safety Primitives

### KillSwitch
Emergency stop. Scopes:
- `SESSION_ABORT` — this session
- `AGENT_ABORT` — one agent
- `SYSTEM_HALT` — all sessions
- `BUDGET_AUTO_HALT` — triggered on exhaustion

Raises `KillSwitchActiveError`.

### ConcurrencyGuard
Serializes overlapping work paths; raises `CycleAlreadyActiveError` or `ResourceLockedError` on contention.

### Precondition
Expected resource state before execution: `resource_id`, `expected_state`, `provider_id`. Built-in providers: `file_sha256` (SHA-256 hash), `env` (env var value). Hosts implement custom providers. Failure raises `PreconditionFailedError` + emits `PRECONDITION_FAILED`.

---

## Agents

### AgentMetadata
Identity + capability declaration: `id`, `name`, `version`, `tags`, `capabilities`.

### AgentCapability
What an agent may do: `action` (ActionValue) + `constraints`. `is_capable()` is the single source of truth — delegation never widens this set.

### DelegationGuard
Governs task hand-offs between agents. Target can never gain source agent's authority ("delegation does not elevate trust").

### AgentSessionRevocation
Fine-grained revocation within a session — does not deregister globally.

---

## Events & Audit

### EventStore
Append-only ledger. Each `EventFrame`: `event_id`, `session_id`, `seq` (monotonic), `kind` (EventKind), `agent_id`, `payload`, `state_bearing` (bool).

### state_bearing
`true` on an event means durable state is at stake — persistence failure MUST raise, never be swallowed. Core safety invariant enforced by Semgrep rules.

### Key EventKinds
Lifecycle: `CYCLE_STARTED/COMPLETED/RECOVERED`, `SESSION_ABORTED`  
Risk: `RISK_ASSESSED`, `SESSION_RISK_ESCALATED`  
Approval: `APPROVAL_REQUESTED/GRANTED/DENIED/TIMEOUT/REVOKED`  
Execution: `EXECUTION_STARTED/COMPLETED`, `PRECONDITION_FAILED`  
Budget: `BUDGET_EXHAUSTED`, `TOKEN_BUDGET_EXHAUSTED`, `TOKEN_USAGE_RECORDED`  
Kill switch: `KILL_SWITCH_TRIGGERED`, `AGENT_REVOKED/REINSTATED`  
MCP: `TOOL_CALL_RECEIVED/ALLOWED/BLOCKED`

---

## Evaluators

### Evaluator (protocol)
Pluggable policy check: `evaluate_proposal()` → `EvaluatorResult`. Optional `evaluate_tool_result()` for response-phase screening.

### EvaluatorRegistry
Discovers evaluators via manual registration or `agent_control_plane.evaluators` entry-point group.

### Built-in evaluators
`RegexEvaluator`, `ListEvaluator`, `EgressEvaluator` (destination + operation allowlist), `ResponseEvaluator` (screens tool output).

### ParallelPolicyEvaluator
Runs all evaluators concurrently with cancel-on-first-deny semantics.

---

## MCP Integration

### McpGateway
Governs MCP tool calls through the full proposal flow. Wraps `ToolExecutor`.

### McpGovernanceError subclasses
`PolicyDeniedError`, `ApprovalRequiredError` (carries `ticket_id`), `BudgetDeniedError`, `KillSwitchActiveError`, `SteeringRequiredError` (carries `SteeringContext`), `PreconditionFailedError`, `ToolExecutionError`, `ToolResultRejectedError`.

### ToolCallContext
Normalized MCP request: `tool_name`, `arguments`, `agent_id`, `session_id`, `estimated_cost`, `preconditions`, identity fields.

---

## High-Level APIs

### ControlPlaneSetup
Builder: `ControlPlaneSetup(database_url=..., governance=GovernanceConfig(...), ...).build()` → `ResilientControlPlane`.

### Gateway decomposition (v0.18)
Focused APIs with ≤ 11 public methods each:
- `SessionGateway` — create/resume/pause/abort sessions
- `ApprovalGateway` — tickets: create/get/decide/revoke
- `BudgetGateway` — check/reserve/charge, query ledger
- `AgenticGateway` — policy simulation, proposal routing, manual execution
- `LifecycleGateway` (async) — session recovery, timeout escalation
- `MaintenanceGateway` (async) — crash recovery, integrity checks
- `ObserverGateway` — query events, export telemetry

### ResilientControlPlane
Wraps gateways with fail-open/fail-closed error handling per operation category:
- `STATE_BEARING`, `BUDGET` → `FAIL_CLOSED` (raise on error)
- `TELEMETRY`, `QUERY` → `FAIL_OPEN` (return defaults, log warning)

Both sync (`ResilientControlPlane`) and async (`AsyncResilientControlPlane`) variants exist.

---

## Storage & Architecture

### Repository pattern
Engines depend on **protocols** (`storage/protocols.py`), not concrete backends → swappable. Concrete: `SyncSqlAlchemyUnitOfWork` (SQLite/Postgres sync), `AsyncSqlAlchemyUnitOfWork` (async).

### ModelRegistry
Host apps register ORM models at startup; engines resolve by name. Avoids hardcoded ORM coupling.

### Transaction ownership
Engines do **not** own transactions — caller manages begin/commit. Engines use `FOR UPDATE` row locks in critical paths.

### Module boundaries (import-linter enforced)
`engine/` must not import concrete storage — only protocols. Semgrep enforces state-bearing fail-closed invariants.

---

## Key File Locations

```
src/agent_control_plane/
  engine/          # Core engines (policy, routing, approvals, budget, risk, etc.)
  evaluators/      # Pluggable evaluator framework + builtins
  mcp/             # McpGateway + error types
  storage/         # Repository protocols + SQLAlchemy backends
  types/           # Pydantic DTOs, enums, type aliases (AgentId, ResourceId, etc.)
  models/          # ORM mixins + ModelRegistry
  recovery/        # CrashRecovery, TimeoutEscalation
  experimental/    # Pre-1.0 features
  setup.py         # ControlPlaneSetup builder
  sync.py          # Sync facades + gateways
  async_facade.py  # Async facades + gateways
  resilient.py     # ResilientControlPlane (sync)
  async_resilient.py  # AsyncResilientControlPlane

docs/
  architecture.md, operations_runbook.md, security_model.md
  adr/             # Architectural decision records (ADR-0001 through ADR-0010)
```
