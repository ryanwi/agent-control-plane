# agent-control-plane

[![CI](https://github.com/ryanwi/agent-control-plane/actions/workflows/ci.yml/badge.svg)](https://github.com/ryanwi/agent-control-plane/actions/workflows/ci.yml)

Safety and approval controls for AI agents.

The **control plane** decides when/how an agent may act. The **data plane** executes side effects.

## Watch Demo

[Watch interactive terminal recording](https://asciinema.org/a/Mrl2E8gMbNLzNKuM)

[![Agent story terminal demo](docs/demo/control-plane-agent-story.gif)](https://asciinema.org/a/Mrl2E8gMbNLzNKuM)

## Why This Exists

Most agent stacks have strong execution layers but weak governance. This package provides:

- Deterministic policy enforcement before execution.
- Human/risk approval gates for high-impact actions.
- Budget guardrails and kill-switch semantics.
- Durable event history for audit, replay, and recovery.

Good fit:

- Platform teams running production agent workflows.
- Teams needing explicit human-in-the-loop and policy controls.
- Multi-agent systems requiring auditable decisions.

Less useful:

- One-off demos with no side effects.
- Prompt/tooling projects that do not need governance.

## Install

```bash
pip install agent-control-plane
```

## Local Dev

```bash
uv sync --extra dev
make check
```

## Quickstart

Use the runnable sync quickstart:

```bash
uv run python examples/quickstart_sync.py
```

ACP-first continuous loop examples:

```bash
uv run python examples/single_agent_continuous_loop.py
uv run python examples/multi_agent_continuous_loop.py
uv run python examples/continuous_loop_governance.py
uv run python examples/long_running_autonomous_agent.py --horizon day
```

Optional SDK integrations (requires provider SDK + API key):

```bash
uv run python examples/openai_agents_sdk_integration.py
uv run python examples/claude_agent_sdk_integration.py
```

For the narrated terminal walkthrough used in the demo video:

```bash
make demo-asciicast-agent
```

## Core Capabilities

- Policy and routing: `PolicyEngine`, `ProposalRouter`
- Human approvals: `ApprovalGate`, scoped ticket decisions
- Budget enforcement: `BudgetTracker`
- Token governance: `TokenBudgetTracker` (identity-scoped token/cost budgets), `ModelGovernor` (model tier access policy)
- Concurrency and kill switches: `ConcurrencyGuard`, `KillSwitch`
- Durable events and replay: `EventStore`
- Session lifecycle and recovery: `SessionManager`, `CrashRecovery`, `TimeoutEscalation`
- Host wrappers: `ControlPlaneFacade` (sync), `AsyncControlPlaneFacade` (async)

## Runtime Notes

- Treat `state_bearing=True` events as fail-closed.
- Prefer `ScopedModelRegistry` for production embedding.
- Use SQLite for local/single-process; use Postgres for multi-worker production.

## Docs & API

- Architecture: [docs/architecture.md](docs/architecture.md)
- Operations runbook: [docs/operations_runbook.md](docs/operations_runbook.md)
- Continuous operation playbook (1h/day/week/month): [docs/continuous_operation_playbook.md](docs/continuous_operation_playbook.md)
- Security model: [docs/security_model.md](docs/security_model.md)
- Identity integration: [docs/integration_identity.md](docs/integration_identity.md)
- Compatibility posture: [docs/compatibility.md](docs/compatibility.md)
- OpenAPI contract (companion gateway): [docs/openapi/control-plane-v1.yml](docs/openapi/control-plane-v1.yml)
- Public API exports: [src/agent_control_plane/__init__.py](src/agent_control_plane/__init__.py)

## Examples

- Sync quickstart: [examples/quickstart_sync.py](examples/quickstart_sync.py)
- Async quickstart: [examples/quickstart.py](examples/quickstart.py)
- Single-agent continuous loop: [examples/single_agent_continuous_loop.py](examples/single_agent_continuous_loop.py)
- Multi-agent continuous loop: [examples/multi_agent_continuous_loop.py](examples/multi_agent_continuous_loop.py)
- Asciicast sync demo: [examples/asciinema_sync_demo.py](examples/asciinema_sync_demo.py)
- Continuous-loop governance example: [examples/continuous_loop_governance.py](examples/continuous_loop_governance.py)
- Long-running autonomous example: [examples/long_running_autonomous_agent.py](examples/long_running_autonomous_agent.py)
- OpenAI Agents SDK integration: [examples/openai_agents_sdk_integration.py](examples/openai_agents_sdk_integration.py)
- Claude Agent SDK integration: [examples/claude_agent_sdk_integration.py](examples/claude_agent_sdk_integration.py)
- Asciicast story runner: [scripts/run_asciicast_agent_story.sh](scripts/run_asciicast_agent_story.sh)
- Audit replay: [examples/audit_viewer.py](examples/audit_viewer.py)
- Token governance demo: [examples/token_governance_demo.py](examples/token_governance_demo.py)
- MCP gateway demo: [examples/mcp_tool_gateway.py](examples/mcp_tool_gateway.py)
- Companion REST/dashboard starter: [examples/companion_gateway](examples/companion_gateway)

## License

MIT
