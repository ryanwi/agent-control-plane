# Compatibility & Upgrading (Pre-1.0)

This project is currently pre-1.0 and iterating quickly. This page is both the compatibility
**policy** and the task-oriented **upgrade guide**. `CHANGELOG.md` remains the per-version
source of truth; this page consolidates the cross-version migrations into one place.

## Versioning posture

- Minor releases may include breaking API changes.
- Patch releases should remain behaviorally compatible unless a critical fix requires otherwise.

## Required release hygiene

- **No silent breaking changes.**
- Any breaking change must be called out in `CHANGELOG.md` with clear migration notes.
- If public exports or integration contracts change, update both:
  - `README.md`
  - `docs/architecture.md`
- Add the migration steps for any breaking change to the **Upgrading** section below.

## Experimental namespaces

Contracts under `agent_control_plane.experimental.*` are intentionally non-stable and may change rapidly between minor releases.

---

# Upgrading

The task-oriented "how do I move my code" companion to `CHANGELOG.md`.

## Quick path

1. Bump the pin (e.g. `agent-control-plane>=0.21.0,<0.22`) and sync.
2. Apply the breaking migrations below for the versions you actually cross. Let `mypy` and
   your test suite drive — most breaks surface as import/attribute/type errors.
3. Optionally adopt the new security capabilities (last section).
4. Verify against a real backend (SQLite is fine) and your telemetry pipeline.

The import package is `agent_control_plane` (underscores); the distribution is
`agent-control-plane` (hyphens). A dependency search for only the hyphenated name will miss
an `import agent_control_plane`.

## Breaking changes

### 0.21.0 — security defaults tightened (three breaking changes)

**`McpGatewayConfig.auto_create_sessions` is now `False`.**

Any `handle_tool_call` call that omits `session_id` now raises `PolicyDeniedError`.

```python
# before (implicitly created a session with max_cost=10000)
gateway.handle_tool_call(ToolCallContext(tool_name="status"))

# after — explicit session required
sid = cp.create_session("my-session", max_cost=Decimal("100"))
gateway.handle_tool_call(ToolCallContext(tool_name="status", session_id=sid))

# or opt back in
config = McpGatewayConfig(policy_snapshot=policy, auto_create_sessions=True)
```

**`EventConfig.unknown_event_policy` default is now `RAISE`.**

`ControlPlaneSetup(...).build()` and `build_async()` will raise `UnknownAppEventError`
for any `emit_app` call whose event name is not in `event_map`.

```python
# before — silently returned None for unmapped events
cp = ControlPlaneSetup(db_url, events=EventConfig(event_map={"job_started": ...})).build()
cp.sessions.emit_app(sid, "unknown_xyz", {})  # returned None

# after — raises UnknownAppEventError for unmapped events
# Fix: either add all used event names to event_map, or:
events = EventConfig(
    event_map={"job_started": EventKind.CYCLE_STARTED},
    unknown_event_policy=UnknownAppEventPolicy.IGNORE,  # restores old behaviour
)
```

**`BUDGET` category in MIXED resilience mode is now `FAIL_CLOSED`.**

A DB error on `check_budget` now raises instead of returning `True`.

```python
# before — DB failure on check_budget returned True in MIXED mode
rcp = ResilientControlPlane(facade, mode=ResilienceMode.MIXED)

# after — DB failure raises (correct security behaviour)
# To restore the old behaviour:
from agent_control_plane.types.enums import OperationCategory, ResilienceMode
rcp = ResilientControlPlane(
    facade,
    mode=ResilienceMode.MIXED,
    category_overrides={OperationCategory.BUDGET: ResilienceMode.FAIL_OPEN},
)
```

### 0.18.0 — facades split into focused gateways

`ControlPlaneFacade` (sync) and `AsyncControlPlaneFacade` no longer expose operations as
flat methods. Each call moves onto a focused sub-gateway:

| Gateway | Operations |
|---|---|
| `.sessions` | `open_session`, `close_session`, `abort_session`, `emit`, `emit_app`, `replay`, `get_session`, `kill_session`, `kill_system` |
| `.lifecycle` | `activate_session`, `pause_session`, `resume_session`, `acquire_cycle`, `release_cycle`, `set_active_cycle` |
| `.approvals` | `create_ticket`, `create_proposal`, `approve_ticket`, `deny_ticket`, `get_ticket`, `list_tickets`, `get_pending_tickets`, `get_proposal`, `list_proposals`, `expire_timed_out_tickets` |
| `.budget` | `check_budget`, `increment_budget`, `get_remaining_budget` |
| `.agentic` | goals, plans, evaluations, guardrails, handoff, checkpoints |
| `.observer` | `list_sessions`, `get_state_change_feed`, `get_health_snapshot`, `get_operational_scorecard` |
| `.maintenance` | `recover_stuck_sessions`, `check_stuck_cycles` |
| `.agents` *(added 0.20.0)* | `revoke`, `reinstate`, `is_revoked` |

```python
# before
sid = await facade.open_session("s")
await facade.activate_session(sid)
# after
sid = await facade.sessions.open_session("s")
await facade.lifecycle.activate_session(sid)
```

### 0.18.0 — `EventStore.append` / event-repo `append` take `EventMetadata`

Individual attribution kwargs are now a single frozen dataclass.

```python
# before
await event_store.append(session_id, kind, payload, agent_id=a, correlation_id=c)
# after
from agent_control_plane import EventMetadata
await event_store.append(session_id, kind, payload, metadata=EventMetadata(agent_id=a, correlation_id=c))
```

### 0.17.0 — facade `emit()` takes `EmitMetadata`

The seven attribution kwargs (`agent_id`, `correlation_id`, `idempotency_key`,
`state_bearing`, `policy_snapshot_id`, `action_id`, `extra`) become one `EmitMetadata`.

```python
# before
facade.emit(sid, kind, payload, agent_id=a, state_bearing=True)
# after
from agent_control_plane import EmitMetadata
facade.sessions.emit(sid, kind, payload, metadata=EmitMetadata(agent_id=a, state_bearing=True))
```

### 0.17.0 — `ControlPlaneSetup` takes three sub-configs

13 flat kwargs → `GovernanceConfig` (action names, policy, token budgets), `EventConfig`
(event map, unknown-event policy), and `ResilienceConfig` (mode + per-category overrides),
all exported from `agent_control_plane`.

### 0.17.0 — `EventFrame.event_kind` → `EventFrame.kind`

Rename every `frame.event_kind` access on event frames to `frame.kind`. The OTel attribute
key `cp.event_kind` and the ORM column name are unchanged — only the DTO attribute renamed.

### 0.17.0 — telemetry span name changed

`"agent_control_plane.event"` → `"agent_control_plane.governance"`. Update any OTel span-name
filters, dashboards, or alerts targeting the old name.

## Behavior changes

### 0.17.1 — session-state integrity is validated on resume/activate

`activate_session`, `resume_session`, and the crash-recovery startup sweep now validate
persisted session state and **fail closed** — a corrupt/tampered session (e.g. negative
counters) raises `SessionStateIntegrityError` and emits a state-bearing
`SESSION_STATE_INVALID` event instead of silently proceeding. If you resume or reactivate
sessions, handle that exception on those paths.

## New opt-in security capabilities

All additive — adopt what fits your threat model.

- **Tool-output inspection (0.16.0)** — pass `response_evaluators=[...]` to `McpGateway`. The
  built-in `RegexResponseEvaluator` screens tool *return values* (keys and values) for
  injection/exfil markers and non-allowlisted outbound URLs, failing closed before the output
  re-enters the model context.
- **Egress capability-grant (0.17.0)** — `EgressEvaluator` models egress as a capability grant,
  not a destination filter: reaching an allowlisted host is necessary but not sufficient; the
  specific capability must also be granted.
- **Approval-fatigue telemetry (0.19.0)** — the operational scorecard exposes
  `approval_grant_rate` (+ `approvals_granted`/`approvals_denied`); `export_scorecard()` emits
  `cp.approval_grant_rate`. Alert if it trends toward ~1.0 (rubber-stamping).
- **Delegation does not elevate trust (0.19.0)** — authorization resolves through
  `AgentMetadata.is_capable` (the agent's own capabilities only). Delegation and handoff are
  advisory/audit records that never widen a target's authority. Don't treat a sub-agent's
  output as higher-trust than any other input.
- **Per-session agent revocation (0.19.0 primitive, enforced in 0.20.0)** — revoke one agent
  within one session without deregistering it globally or aborting the session:
  `facade.agents.revoke(session_id, agent_id, reason=...)` / `reinstate` / `is_revoked`. A
  revoked agent is blocked fail-closed in `McpGateway` tool calls and `ProposalRouter.route()`.

## Verify

- Run the full test suite and type checker; fix everything the migration surfaces.
- Exercise a hot path end-to-end against a real backend: open → activate → propose → approve
  → emit → replay (and a revoke → blocked path if you adopt revocation).
- Confirm telemetry still emits (new span name + `cp.*` attributes) and dashboards are updated.
