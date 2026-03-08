from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_control_plane.sync import ControlPlaneFacade
from agent_control_plane.types.enums import EvaluationDecision, EventKind, GuardrailPhase


def _facade(tmp_path: Path) -> ControlPlaneFacade:
    db_file = tmp_path / "agentic_primitives.db"
    facade = ControlPlaneFacade.from_database_url(f"sqlite:///{db_file}")
    facade.setup()
    return facade


def test_checkpoint_and_rollback_flow(tmp_path: Path) -> None:
    facade = _facade(tmp_path)
    sid = facade.open_session("checkpoint-demo")

    cp = facade.create_checkpoint(sid, label="before-action", metadata={"stage": "init"})
    assert cp.session_id == sid

    page = facade.list_checkpoints(sid)
    assert page.items
    assert page.items[0].id == cp.id

    result = facade.rollback_to_checkpoint(sid, cp.id, reason="operator rollback")
    assert result.session_id == sid
    assert result.to_seq == cp.event_seq


def test_goal_plan_and_progress_flow(tmp_path: Path) -> None:
    facade = _facade(tmp_path)
    sid = facade.open_session("planning-demo")

    goal = facade.create_goal(sid, name="close incident", description="resolve customer ticket")
    plan = facade.create_plan(sid, goal.id, title="incident workflow", steps=["triage", "fix", "verify"])

    facade.start_plan_step(sid, plan.id, step_index=0)
    facade.complete_plan_step(sid, plan.id, step_index=0, notes="triaged")

    progress = facade.get_plan_progress(sid, goal.id)
    assert progress.goal.id == goal.id
    assert progress.completed_steps == 1
    assert progress.total_steps == 3


def test_evaluation_guardrail_handoff_and_scorecard(tmp_path: Path) -> None:
    facade = _facade(tmp_path)
    sid = facade.open_session("governance-demo")

    ev = facade.record_evaluation(
        sid,
        operation="approve_ticket",
        decision=EvaluationDecision.BLOCK,
        score=0.21,
        reasons=["insufficient evidence"],
    )
    assert ev.operation == "approve_ticket"

    gd = facade.apply_guardrail(
        sid,
        phase=GuardrailPhase.INPUT,
        allow=False,
        policy_code="CP-GR-001",
        reason="unsafe input",
    )
    assert gd.allow is False

    handoff = facade.request_handoff(
        sid,
        source_agent_id="agent-a",
        target_agent_id="agent-b",
        allowed_actions=["status"],
    )
    assert handoff.accepted is True
    rejected = facade.request_handoff(
        sid,
        source_agent_id="agent-a",
        target_agent_id="agent-c",
        allowed_actions=["status"],
        accepted=False,
    )
    assert rejected.accepted is False

    facade.emit(sid, EventKind.APPROVAL_REQUESTED, {}, state_bearing=False)
    facade.emit(sid, EventKind.APPROVAL_GRANTED, {}, state_bearing=False)
    facade.emit(sid, EventKind.CHECKPOINT_CREATED, {}, state_bearing=False)
    facade.emit(sid, EventKind.ROLLBACK_COMPLETED, {}, state_bearing=False)
    facade.emit(sid, EventKind.EXECUTION_COMPLETED, {"cost": 1.25}, state_bearing=False)
    facade.emit(sid, EventKind.BUDGET_EXHAUSTED, {}, state_bearing=False)
    facade.emit(sid, EventKind.KILL_SWITCH_TRIGGERED, {"reason": "budget_denied"}, state_bearing=False)

    scorecard = facade.get_operational_scorecard(session_id=sid)
    assert scorecard.evaluations_blocked >= 1
    assert scorecard.guardrail_denies >= 1
    assert scorecard.guardrail_allows == 0
    assert scorecard.handoffs_accepted >= 1
    assert scorecard.handoffs_rejected >= 1
    assert scorecard.budget_denied_count >= 1
    assert scorecard.budget_exhausted_count >= 1
    assert scorecard.evaluation_block_reasons.get("insufficient evidence") == 1
    assert scorecard.guardrail_policy_code_counts.get("CP-GR-001") == 1
    assert scorecard.approval_latency_ms_p50 is not None
    assert scorecard.approval_latency_ms_p95 is not None
    assert scorecard.checkpoint_rollback_latency_ms_p50 is not None
    assert scorecard.checkpoint_rollback_latency_ms_p95 is not None
    assert scorecard.avg_cost_per_successful_action == 1.25
    assert scorecard.handoff_accept_rate == 0.5


def test_scorecard_window_filtering(tmp_path: Path) -> None:
    facade = _facade(tmp_path)
    sid = facade.open_session("window-demo")
    facade.emit(sid, EventKind.CYCLE_STARTED, {}, state_bearing=False)
    window_start = datetime.now(UTC) + timedelta(seconds=1)
    scorecard = facade.get_operational_scorecard(session_id=sid, window_start=window_start)
    assert scorecard.total_events == 0
