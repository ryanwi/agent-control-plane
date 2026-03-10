# Continuous Operation Playbook (1h → 30d)

This playbook shows how to run an agent continuously while using ACP as the governance layer.

## Core pattern

Run the **worker continuously**, but rotate ACP **sessions in windows**.

- Worker lifetime: long-lived (days to weeks).
- Session lifetime: bounded control window (hourly or daily).
- All mutating calls use `command_id` for replay-safe retries.
- `state_bearing=True` events stay fail-closed.

This gives you unattended execution without leaving one giant unbounded session open for a month.

## Time horizons

### First hour

- Run one worker and one session.
- Enable budgets and approval gates on risky actions.
- Verify restart replay safety with repeated `command_id`.
- Confirm no state-bearing write failures.

Example:

```bash
uv run python examples/long_running_autonomous_agent.py --horizon hour
```

### First day

- Keep worker continuous.
- Rotate sessions every 1-4 hours.
- Add periodic checkpoints.
- Alert on pending-approval age and budget exhaustion.

Example:

```bash
uv run python examples/long_running_autonomous_agent.py --horizon day
```

### First week

- Move to Postgres and multi-worker deployment.
- Run recovery and timeout escalation on startup and on schedule.
- Run kill-switch and approval-backlog drills.
- Review denial reasons and adjust policy thresholds.

### First month

- Run daily session windows for audit and blast-radius control.
- Keep low-risk actions auto-approved and high-risk actions denied/escalated.
- Track scorecard trends: approvals, denials, budget exhaustions, recovery counts.
- Trigger operator response only on alerts or incident runbooks.

## Different operating angles

### Reliability

- `command_id` on all mutating operations.
- Short session windows with deterministic close/open boundaries.
- Recovery scans before taking traffic.

### Safety

- Risk-based decisioning: deny/escalate sensitive actions.
- Kill at smallest safe scope first (session before system).
- Treat state-bearing write failures as hard failures.

### Cost and throughput

- Check budget before execution; increment after successful execution.
- Tune per-session limits from observed usage.
- Alert on rate changes, not just absolute counts.

### Audit and recovery

- Persist event trail for replay.
- Use periodic checkpoints for fast rollback points.
- Keep correlation IDs and idempotency keys in logs.

## Recommended defaults

- Local/demo: SQLite.
- Production: Postgres.
- Session rotation: start hourly, move to daily once stable.
- Checkpoint cadence: every 12-24 cycles.
- Approval timeout: deny-by-default on expiration.
