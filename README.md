# agent-control-plane

[![CI](https://github.com/ryanwi/agent-control-plane/actions/workflows/ci.yml/badge.svg)](https://github.com/ryanwi/agent-control-plane/actions/workflows/ci.yml)

Embeddable governance framework for autonomous agent runtimes.

In this library, the **control plane** is the authoritative layer that decides when and how an agent may act.
The **data plane** is the execution path that actually sends orders, calls services, or writes external state.

## Why this exists

Most agent stacks have strong data/IO layers but weak governance. This package provides:

- Deterministic policy enforcement before execution.
- Human and risk gates for high-impact actions.
- Budget guardrails and fail-safe stop mechanisms.
- Auditable event logs for replay and recovery.

Who should use this:

- Internal platform teams building production agent runtimes.
- Workflow and orchestration teams that need governance between planning and execution.
- Teams needing explicit human-in-the-loop, risk controls, and auditability.

Who this is less useful for:

- Single-agent demos that do not execute side effects.
- Projects that only need lightweight prompt tooling without approval, policy, or budget constraints.

### Hosted control planes vs this embedded library

This project is for teams that need governance controls without adopting external control-plane infrastructure.

| Dimension | Hosted/SaaS control plane | `agent-control-plane` |
| --- | --- | --- |
| Deployment model | Vendor-managed platform | Embedded library in your runtime |
| Infra ownership | External control plane | Your service/process/database |
| Data residency/control | Vendor-dependent | Stays in your environment |
| Integration style | Platform adoption | Code-level integration |
| Governance primitives | Product-dependent | Policy, approvals, budget, kill switch, audit replay |

Best fit:
- Platform teams embedding governance into existing services and orchestration loops.
- Teams requiring strong operational controls while keeping infra and data boundaries internal.

## Adoption examples (non-trading)

- **Support automation with approvals:** route support tasks to agents, but require approval before account changes or refunds.
- **Research/analysis swarms:** coordinate fact-finding agents and apply scoped approvals for high-risk escalations.
- **CI/CD and platform automation:** enforce policy checks before any infrastructure change and use kill-switches for emergency halts.
- **Data and content pipelines:** guard sensitive write actions with session/resource locks and recoverable state transitions.

## Install

```bash
pip install agent-control-plane
```

## Local development setup

Preferred workflow (consistent interpreter + dependencies):

```bash
uv sync --extra dev
uv run pytest -q
```

If you prefer bare `python`/`pytest` commands, install the package in editable mode:

```bash
python -m pip install -e ".[dev]"
pytest -q
```

The repo uses a `src/` layout. If you run commands with a Python interpreter that does not have
the package installed (or you do not use `uv run`), imports like `import agent_control_plane`
will fail with `ModuleNotFoundError`.

## Quick architecture overview

For the full reference design, see [docs/architecture.md](docs/architecture.md).

```mermaid
flowchart LR
  A[Proposal] --> B[Agent Registry]
  B --> C[Policy Engine]
  C --> D[Proposal Router]
  D --> E[Approval Gate]
  E --> F[Budget Tracker]
  F --> G[Concurrency Guard]
  G --> H[Kill Switch Check]
  H --> I[Execution Plane]
  I --> J[Event Store]
  J --> K[Session Manager + Recovery]
```

## Core components

- `AgentRegistry` manages registered agent identities, versions, and capability maps.
- `PolicyEngine` classifies risk, assigns action tier via polymorphic handlers, and evaluates limits.
- `ProposalRouter` resolves policy outcomes and validates agent authorization.
- Routing decisions expose typed `RoutingResolutionStep` values for deterministic audit semantics.
- `DelegationGuard` oversees and audits task hand-offs between different agents.
- `ApprovalGate` manages ticket creation, scoped approvals, expiry, and denial paths.
- `BudgetTracker` enforces session-level cost/count ceilings.
- `KillSwitch` provides session/system/budget emergency stop semantics.
- `ConcurrencyGuard` blocks duplicate work for the same session and resource.
- `EventStore` writes monotonic events and supports non-state-bearing buffering on DB failures.
- `SessionManager`, `CrashRecovery`, and `TimeoutEscalation` preserve continuity after failures.
- `McpGateway` governs MCP tool calls before execution (policy, approvals, budget, audit).

## Naming conventions

`agent-control-plane` uses role-based naming:

- Domain/contract types: suffix-free names (`ActionProposal`, `ApprovalTicket`, `PolicySnapshot`).
- Persistence ORM classes: `*Row` suffix (`ActionProposalRow`, `ApprovalTicketRow`, `PolicySnapshotRow`).
- Transport-specific models should use explicit role names when introduced (for example `CreateProposalRequest`, `ProposalView`), not generic `DTO`.

## Typed extension and alias hooks

- Use `register_risk_limits_extension_schema(...)` to enforce typed validation for `RiskLimits.custom`.
- `RiskLimits.validate_extension()` and `RiskLimits.extension_as()` both fail fast when no extension schema is registered.
- For non-domain payload transforms, use `apply_inbound_aliases(...)` and `apply_outbound_aliases(...)` with a registered alias profile.
- Alias helper rule of thumb:
  - Use profiled model helpers (`model_validate_with_profile`, `model_dump_with_profile`) when payload maps directly to library types.
  - Use `apply_inbound_aliases`/`apply_outbound_aliases` for boundary payloads not modeled as core types (for example, app-specific event envelopes).

## Control-plane lifecycle

1. Proposal enters the control plane with agent identity, intent, and resource scope.
2. **Identity check**: Agent is verified against the `AgentRegistry` for valid capabilities.
3. **Policy check**: Action is classified into a tier (Blocked, Auto-approve, Manual gate).
4. **Router decision**: A deterministic routing decision is emitted with a clear reason.
5. **Approvals**:
   1. If no human gate is needed, the proposal proceeds.
   2. If a manual gate is required, an `ApprovalTicket` is created.
6. **Budgets**: Risk-weighted budget is reserved atomically.
7. **Concurrency**: A resource-specific lock is acquired for the duration of the cycle.
8. **Execution**: The proposal is executed by the downstream data plane.
9. **Audit**: Every step emits a monotonic event to the `EventStore`.

## Failure semantics

- `state_bearing=True` events must fail closed (raise on persistence failure).
- Non-state-bearing telemetry events are buffered when persistence is unavailable.
- Kill switch and crash recovery paths are designed to resolve control locks deterministically.
- Timeout recovery emits escalation events and can pause sessions to prevent runaway execution.

## Identity and Zero Trust guidance

Identity enforcement should happen at your application boundary; governance is then enforced inside the control plane.

- Authenticate callers at the app edge (OIDC/JWT/service identity).
- Authorize allowed operations before constructing control-plane proposals.
- Pass normalized caller/agent identity as `agent_id` on proposals/events.
- Use `UnknownAppEventPolicy.RAISE` for fail-closed event-name handling.
- Use `state_bearing=True` for critical state transitions that must not be dropped.

Related references:
- [Security model](docs/security_model.md)
- [Identity integration guide](docs/integration_identity.md)
- [Operations runbook](docs/operations_runbook.md)
- [Architecture reference](docs/architecture.md)

## Installation in host application

1. Define or import SQLAlchemy models for control plane persistence.
2. Register models via `ModelRegistry.register("ModelName", YourModel)`.
3. Create and persist session/policy records with your service transaction manager.
4. Execute every control-plane transition through the engines above, not directly on models.
5. Call recovery handlers during startup and on stuck-cycle monitors.

### DB deployment guide (dev vs prod)

Use the control plane as embedded application state, not as an external service.

- Local development baseline:
  - Use SQLite file storage (`sqlite:///./control_plane.db` or `sqlite+aiosqlite:///./control_plane.db`).
  - Best for single-process development, demos, and integration tests.
- Production baseline:
  - Use Postgres (`postgresql+psycopg://...` for sync or `postgresql+asyncpg://...` for async).
  - Use for multi-worker/multi-instance deployments, stronger concurrency behavior, and operational durability.

Switch from SQLite to Postgres when any of the following is true:

- You run more than one worker/process against the same control-plane DB.
- You need managed backups, PITR, and high-availability controls.
- You need predictable operational behavior under sustained concurrent writes.

### Storage note

The control plane is designed around durable state transitions (sessions, tickets, budgets,
cycle locks, and sequencing. The engine/recovery layer depends on repository protocols, and the
package ships SQLAlchemy async and sync implementations:

- `AsyncSqlAlchemyUnitOfWork`
- `SyncSqlAlchemyUnitOfWork`
- `SyncControlPlane` convenience facade
- `ControlPlaneFacade` high-level sync host wrapper
- `AsyncControlPlaneFacade` high-level async host wrapper

Recommended startup sequence:

```text
register_models()
build UnitOfWork (async or sync)
construct engines with repos
session_manager.create_session(...)
session_manager.create_policy(...)
crash_recovery.run_recovery(...)
timeout_escalation.scan_and_recover(...)
```

### Reliability checklist

- Startup sequence:
  - Register models, initialize schema, run crash/timeout recovery, then accept traffic.
- Transaction boundary:
  - Keep control-plane write operations inside a host-managed transaction/UoW boundary.
  - Do not mix direct ORM writes with facade/engine transitions for the same state changes.
- Concurrency model:
  - SQLite: single-process/small-scale usage.
  - Postgres: recommended for concurrent workers and production operations.
- Fail-closed critical path:
  - `state_bearing=True` events must raise on persistence failure.
  - Treat these failures as blocking errors, never as best-effort telemetry.
- Monitoring minimums:
  - active sessions, pending approvals, budget exhaustion/denials, kill-switch triggers, stuck cycles.

## Troubleshooting

`ModuleNotFoundError: No module named 'agent_control_plane'`

- Cause: running `python` directly without an editable install and outside `uv run`.
- Fix: run with `uv run ...` or execute `python -m pip install -e ".[dev]"`.

`python -m pytest` says `No module named pytest`

- Cause: dependencies not installed in that interpreter.
- Fix: run `uv sync --extra dev` and use `uv run pytest`, or install `.[dev]` into that interpreter.

## 5-minute integration sketch (async)

```python
from decimal import Decimal
from uuid import uuid4
from sqlalchemy.ext.asyncio import AsyncSession

from agent_control_plane import (
    ActionName,
    ApprovalGate,
    BudgetTracker,
    ConcurrencyGuard,
    EventKind,
    EventStore,
    PolicyEngine,
    ProposalRouter,
    SessionManager,
    AsyncSqlAlchemyUnitOfWork,
)
from agent_control_plane.types import ActionProposal, PolicySnapshot


async def handle_proposal(db_session: AsyncSession, request: dict) -> None:
    uow = AsyncSqlAlchemyUnitOfWork(db_session)
    policy_snapshot = PolicySnapshot(**request["policy_snapshot"])
    session_manager = SessionManager(uow.session_repo)
    event_store = EventStore(uow.event_repo)
    approval_gate = ApprovalGate(event_store, uow.approval_repo, uow.proposal_repo)
    budget = BudgetTracker(uow.session_repo)
    guard = ConcurrencyGuard(uow.session_repo, uow.proposal_repo)

    policy_id = await session_manager.create_policy(
        action_tiers=policy_snapshot.action_tiers.model_dump(mode="json"),
        risk_limits=policy_snapshot.risk_limits.model_dump(mode="json"),
        execution_mode=policy_snapshot.execution_mode,
        approval_timeout_seconds=policy_snapshot.approval_timeout_seconds,
        auto_approve_conditions=policy_snapshot.auto_approve_conditions.model_dump(mode="json"),
    )
    session = await session_manager.create_session(
        session_name=f"demo-session-{uuid4()}",
        execution_mode=policy_snapshot.execution_mode,
        max_cost=Decimal("1000"),
        max_action_count=100,
        policy_id=policy_id,
    )

    proposal = ActionProposal(
        session_id=session.id,
        agent_id=request.get("agent_id"),
        resource_id=request["resource_id"],
        resource_type=request.get("resource_type", "resource"),
        decision=ActionName.REBOOT_INSTANCE,
        reasoning=request.get("reasoning", "auto proposal"),
        weight=Decimal(request.get("weight", "0")),
        score=Decimal(request.get("score", "0")),
    )
    
    # New: Router is now async and can validate agent identity
    route = await ProposalRouter(PolicyEngine(policy_snapshot)).route(proposal)
    
    await guard.check_resource_lock(session.id, proposal.resource_id)
    if not await budget.check_budget(session.id, cost=proposal.weight):
        return
        
    await budget.increment(session.id, cost=proposal.weight)
    await guard.acquire_cycle(session.id, cycle_id=uuid4())
    
    await event_store.append(
        session_id=session.id,
        event_kind=EventKind.CYCLE_STARTED,
        payload={"proposal_id": str(proposal.id), "tier": route.tier.value},
        state_bearing=True,
    )
    await guard.release_cycle(session.id)
    await uow.commit()
```

For a native sync host, use `SyncControlPlane` or `ControlPlaneFacade` and `examples/quickstart_sync.py`.
For async hosts (FastAPI/async workers), use `AsyncControlPlaneFacade`.

`AsyncControlPlaneFacade` now also covers the common operational flows that previously required direct UoW access:
- session transitions (`activate_session`, `pause_session`, `resume_session`, `list_sessions`)
- cycle coordination (`acquire_cycle`, `release_cycle`, `set_active_cycle`)
- proposals/approvals (`create_proposal`, `create_ticket`, `approve_ticket`, `deny_ticket`, `get_pending_tickets`, `expire_timed_out_tickets`)
- policy creation and recovery helpers (`create_policy`, `recover_stuck_sessions`, `check_stuck_cycles`)

`ControlPlaneFacade` also exposes proposal/approval write flows for sync hosts:
- `create_proposal`
- `create_ticket`
- `approve_ticket`
- `deny_ticket`

For retry-safe proposal inserts, pass a stable `command_id`:

```python
from agent_control_plane import proposal_command_id

command_id = proposal_command_id(
    session_id=proposal.session_id,
    resource_id=proposal.resource_id,
    resource_type=proposal.resource_type,
    decision=proposal.decision,
)
created = facade.create_proposal(proposal, command_id=command_id)
```

Agentic governance primitives are also available on sync/async facades:
- checkpoint/rollback (`create_checkpoint`, `list_checkpoints`, `rollback_to_checkpoint`)
- goal/planning (`create_goal`, `create_plan`, `start_plan_step`, `complete_plan_step`, `get_plan_progress`)
- evaluation/guardrails/handoff (`record_evaluation`, `apply_guardrail`, `request_handoff`)
- operational scorecards (`get_operational_scorecard`)

`get_operational_scorecard(...)` includes extended effectiveness metrics:
- guardrail allow/deny counts and policy-code distribution
- evaluation block-reason distribution
- budget denied/exhausted counters
- approval and checkpoint-rollback latency percentiles
- average cost per successful action and handoff acceptance rate

Benchmark and experimentation helpers are available for closed-loop policy tuning:
- benchmark types (`BenchmarkScenarioSpec`, `BenchmarkRunSpec`, `BenchmarkRunResult`, `FitnessWeights`)
- benchmark runner utilities (`run_benchmark`, `run_batch`, `hash_config`, `WeightedFitnessEvaluator`)

Policy and telemetry integration helpers:
- policy protocols (`EvaluatorPolicy`, `GuardrailPolicy`) with defaults (`ThresholdEvaluatorPolicy`, `PassThroughGuardrailPolicy`)
- telemetry bridges (`export_event`, `export_scorecard`) for OTel-compatible tracer/meter adapters
- experimental capability contracts for deployment/runtime composition:
  - `agent_control_plane.experimental.capabilities`
  - builder service bundles expose `get_capabilities()` for detection only (non-authoritative; no enforcement)

Example (detection only):

```python
from agent_control_plane.builders import build_session_event_budget
from agent_control_plane.experimental.capabilities import StaticCapabilityProvider, capability_set_from_mapping

provider = StaticCapabilityProvider(
    capability_set_from_mapping({"managed_operations": {"version": "exp-1"}})
)
services = build_session_event_budget(
    session_repo=session_repo,
    event_repo=event_repo,
    capability_provider=provider,
)
if services.get_capabilities().has("managed_operations"):
    print("Managed operations integration is available")
```

`SyncControlPlane.kill()` and `SyncControlPlane.kill_all()` return `KillResult`.
`SyncControlPlane.emit_event()` / `replay_events()` provide first-class sync event operations.
`SyncControlPlane.emit_app_event()` supports boundary mapping via `AppEventMapper`/`DictEventMapper`.
`SyncControlPlane.complete_session()` / `abort_session()` return `SessionLifecycleResult`.

## Feed projection consumer loop

`get_state_change_feed(...)` is the incremental projection interface for consumers (agents, workers, read models).
It returns only `state_bearing=True` events and a cursor you checkpoint after processing.

```python
cursor = load_checkpoint(default=0)
while True:
    page = facade.get_state_change_feed(cursor=cursor, limit=100)
    if not page.items:
        break
    for item in page.items:
        session_id = item.event.session_id
        tickets = facade.list_tickets(session_id=session_id, limit=200, offset=0).items
        proposals = facade.list_proposals(session_id=session_id, limit=200, offset=0).items
        project_session_state(session_id, tickets=tickets, proposals=proposals)
        cursor = item.cursor
        save_checkpoint(cursor)
```

## MCP tool-call gateway (v0.4)

Use `McpGateway` to enforce governance before MCP tool execution.

```python
from decimal import Decimal
from agent_control_plane.mcp import McpGateway, McpGatewayConfig, ToolCallContext, ToolCallResult, ToolPolicyMap
from agent_control_plane.sync import SyncControlPlane
from agent_control_plane.types.enums import ActionName
from agent_control_plane.types.policies import ActionTiers, PolicySnapshot

cp = SyncControlPlane("sqlite:///./control_plane.db")
cp.setup()
session_id = cp.create_session("mcp-runtime", max_cost=Decimal("100"), max_action_count=50)

policy = PolicySnapshot(action_tiers=ActionTiers(auto_approve=[ActionName.STATUS]))

class Executor:
    def execute(self, context):
        return ToolCallResult(ok=True, output={"status": "ok"}, cost=Decimal("0.2"))

gateway = McpGateway(
    cp,
    Executor(),
    ToolPolicyMap({"status": ActionName.STATUS}),
    config=McpGatewayConfig(policy_snapshot=policy),
)

result = gateway.handle_tool_call(
    ToolCallContext(tool_name="status", session_id=session_id, estimated_cost=Decimal("0.1"))
)
```

Unknown tools are denied by default (fail-closed).

Import enums from `agent_control_plane.types`:

```python
from agent_control_plane.types import EventKind, UnknownAppEventPolicy
```

## Registry strategy

- Quickstart: rely on defaults (reference models auto-registered).
- Production integration: create and pass an explicit `ScopedModelRegistry` per app/runtime to avoid global registry coupling.

## ORM integration

Mixin examples and full schema details are in `agent_control_plane/models/mixins.py`.

```python
from sqlalchemy.orm import DeclarativeBase
from agent_control_plane.models.mixins import ControlSessionMixin, ControlEventMixin


class Base(DeclarativeBase):
    pass


class ControlSession(Base, ControlSessionMixin):
    __tablename__ = "control_sessions"


class ControlEvent(Base, ControlEventMixin):
    __tablename__ = "control_events"
```

## What is new compared to standard orchestration

- It governs execution, rather than only wiring agents together.
- It treats safety decisions as first-class events and audit state.
- It supports explicit recovery and stop semantics for production operation.
- This package is designed as a reusable control-plane component for an agent harness, not a single-product feature.

## Docs and API

- Architecture and lifecycle reference: [docs/architecture.md](docs/architecture.md)
- Compatibility posture (pre-1.0): [docs/compatibility.md](docs/compatibility.md)
- Canonical HTTP contract for companion gateways: [docs/openapi/control-plane-v1.yml](docs/openapi/control-plane-v1.yml)
- Public API surface: [`src/agent_control_plane/__init__.py`](src/agent_control_plane/__init__.py)
- Domain Examples:
  - Finance: [`examples/finance_agent.py`](examples/finance_agent.py)
  - Cloud Ops: [`examples/cloud_ops_agent.py`](examples/cloud_ops_agent.py)
  - Support: [`examples/support_agent.py`](examples/support_agent.py)
  - SRE: [`examples/sre_agent.py`](examples/sre_agent.py)
  - Content Moderation: [`examples/moderation_agent.py`](examples/moderation_agent.py)
  - Cybersecurity: [`examples/cyber_agent.py`](examples/cyber_agent.py)
- Validation & Stress Tests:
  - Crash Recovery: [`examples/zombie_agent.py`](examples/zombie_agent.py)
  - Kill Switches: [`examples/panic_agent.py`](examples/panic_agent.py)
  - Timeout Escalation: [`examples/ghosted_agent.py`](examples/ghosted_agent.py)
  - Multi-Agent Delegation: [`examples/multi_agent_delegation.py`](examples/multi_agent_delegation.py)
  - Concurrency/Budget: [`examples/rate_limited_agent.py`](examples/rate_limited_agent.py)
  - Asset Scoping: [`examples/compliance_agent.py`](examples/compliance_agent.py)
- Utilities:
  - Audit Trail Replay: [`examples/audit_viewer.py`](examples/audit_viewer.py)
  - MCP Gateway Demo: [`examples/mcp_tool_gateway.py`](examples/mcp_tool_gateway.py)
  - Companion REST+Dashboard Starter: [`examples/companion_gateway`](examples/companion_gateway)
    - runnable entrypoint: `uv run uvicorn examples.companion_gateway.main:app --reload --port 8000`

`agent-control-plane` remains library-first. Host HTTP APIs and dashboards in a companion gateway service that maps this
contract to `ControlPlaneFacade` / `AsyncControlPlaneFacade`.

## License

MIT
