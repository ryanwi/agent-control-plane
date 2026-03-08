# Companion Gateway Starter

This folder is a starter for a companion API + dashboard service that wraps `agent-control-plane` facades.

## Goals

- Keep `agent-control-plane` as a library.
- Expose a stable HTTP/API surface for agents/apps.
- Provide a minimal embedded dashboard endpoint (`/dashboard`).

## Contract Source of Truth

- Canonical contract: `docs/openapi/control-plane-v1.yml`

## Start Point

- App factory: `examples/companion_gateway/app.py:create_app`
- Wire your real `AsyncControlPlaneFacade` instance in your host service bootstrap.

## Run Locally

```bash
uv run uvicorn examples.companion_gateway.main:app --reload --port 8000
```

Auth behavior:
- `create_app(...)` defaults to deny-all auth (`401`) unless you provide an auth policy.
- The runnable `main.py` uses `AllowAllAuthPolicy` for convenience when no token is configured.
- To require bearer auth in local runs, set `ACP_GATEWAY_BEARER_TOKEN` before starting.
