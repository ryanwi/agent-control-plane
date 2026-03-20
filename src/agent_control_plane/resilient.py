"""Resilient wrapper for ControlPlaneFacade with fail-open/fail-closed semantics.

Eliminates the try/except boilerplate that consumers independently build
around every control-plane call. See ADR-0009 for design rationale.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any, TypeVar
from uuid import UUID

from agent_control_plane.sync import (
    ControlPlaneFacade,
    KillResult,
    SessionLifecycleResult,
)
from agent_control_plane.types.agentic import (
    ControlPlaneScorecard,
    EvaluationResult,
    Goal,
    GuardrailDecision,
    HandoffResult,
    Plan,
    PlanProgress,
    PlanStep,
    RollbackResult,
    SessionCheckpoint,
)
from agent_control_plane.types.approvals import ApprovalTicket
from agent_control_plane.types.enums import (
    ApprovalDecisionType,
    ApprovalStatus,
    EvaluationDecision,
    EventKind,
    ExecutionMode,
    GuardrailPhase,
    OperationCategory,
    ProposalStatus,
    ResilienceMode,
)
from agent_control_plane.types.frames import EventFrame
from agent_control_plane.types.ids import AgentId, IdempotencyKey
from agent_control_plane.types.proposals import ActionProposal
from agent_control_plane.types.query import Page, SessionHealth, StateChangePage
from agent_control_plane.types.sessions import SessionState

T = TypeVar("T")

_DEFAULT_MIXED_MODES: dict[OperationCategory, ResilienceMode] = {
    OperationCategory.STATE_BEARING: ResilienceMode.FAIL_CLOSED,
    OperationCategory.TELEMETRY: ResilienceMode.FAIL_OPEN,
    OperationCategory.QUERY: ResilienceMode.FAIL_OPEN,
    OperationCategory.BUDGET: ResilienceMode.FAIL_OPEN,
}


class ResilientControlPlane:
    """Wrapper that adds fail-open/fail-closed semantics to ControlPlaneFacade.

    Eliminates the try/except boilerplate that consumers independently
    build around every CP call.

    In MIXED mode (the default):
      - STATE_BEARING ops (session transitions, budget increments) → raise on error
      - TELEMETRY ops (event emission, scorecard) → return None/default, log warning
      - QUERY ops (reads, replays, health checks) → return None/default, log warning
      - BUDGET checks (not increments) → return True, log warning
    """

    def __init__(
        self,
        facade: ControlPlaneFacade,
        mode: ResilienceMode = ResilienceMode.MIXED,
        logger: logging.Logger | None = None,
        category_overrides: dict[OperationCategory, ResilienceMode] | None = None,
    ) -> None:
        self._facade = facade
        self._mode = mode
        self._logger = logger or logging.getLogger(__name__)
        self._category_modes = dict(_DEFAULT_MIXED_MODES)
        if category_overrides:
            self._category_modes.update(category_overrides)

    @property
    def facade(self) -> ControlPlaneFacade:
        """Access the underlying facade for advanced use cases."""
        return self._facade

    def _should_raise(self, category: OperationCategory) -> bool:
        if self._mode == ResilienceMode.FAIL_CLOSED:
            return True
        if self._mode == ResilienceMode.FAIL_OPEN:
            return False
        # MIXED: per-category
        return self._category_modes.get(category, ResilienceMode.FAIL_CLOSED) == ResilienceMode.FAIL_CLOSED

    def _handle_error(self, exc: Exception, method: str, category: OperationCategory, default: T) -> T:
        if self._should_raise(category):
            raise
        self._logger.warning("CP %s failed (%s), returning default: %s", method, exc, default)
        return default

    # ── Session lifecycle (STATE_BEARING) ──────────────────────────

    def setup(self) -> None:
        self._facade.setup()

    def close(self) -> None:
        self._facade.close()

    def open_session(
        self,
        name: str,
        *,
        max_cost: Decimal = Decimal("10000"),
        max_action_count: int = 50,
        execution_mode: ExecutionMode = ExecutionMode.DRY_RUN,
        command_id: IdempotencyKey | None = None,
    ) -> UUID:
        try:
            return self._facade.open_session(
                name,
                max_cost=max_cost,
                max_action_count=max_action_count,
                execution_mode=execution_mode,
                command_id=command_id,
            )
        except Exception as exc:
            return self._handle_error(exc, "open_session", OperationCategory.STATE_BEARING, UUID(int=0))

    def close_session(
        self,
        session_id: UUID,
        *,
        final_event_kind: EventKind | None = None,
        payload: dict[str, Any] | None = None,
        command_id: IdempotencyKey | None = None,
    ) -> SessionLifecycleResult | None:
        try:
            return self._facade.close_session(
                session_id,
                final_event_kind=final_event_kind,
                payload=payload,
                command_id=command_id,
            )
        except Exception as exc:
            return self._handle_error(exc, "close_session", OperationCategory.STATE_BEARING, None)

    def abort_session(
        self,
        session_id: UUID,
        *,
        reason: str = "Session aborted",
        command_id: IdempotencyKey | None = None,
    ) -> SessionLifecycleResult | None:
        try:
            return self._facade.abort_session(session_id, reason=reason, command_id=command_id)
        except Exception as exc:
            return self._handle_error(exc, "abort_session", OperationCategory.STATE_BEARING, None)

    # ── Telemetry (TELEMETRY) ──────────────────────────────────────

    def emit(
        self,
        session_id: UUID,
        event_kind: EventKind,
        payload: dict[str, Any],
        *,
        state_bearing: bool = False,
        agent_id: AgentId | None = None,
        correlation_id: UUID | None = None,
        routing_decision: dict[str, Any] | None = None,
        routing_reason: str | None = None,
        idempotency_key: IdempotencyKey | None = None,
        command_id: IdempotencyKey | None = None,
    ) -> int | None:
        category = OperationCategory.STATE_BEARING if state_bearing else OperationCategory.TELEMETRY
        try:
            return self._facade.emit(
                session_id,
                event_kind,
                payload,
                state_bearing=state_bearing,
                agent_id=agent_id,
                correlation_id=correlation_id,
                routing_decision=routing_decision,
                routing_reason=routing_reason,
                idempotency_key=idempotency_key,
                command_id=command_id,
            )
        except Exception as exc:
            return self._handle_error(exc, "emit", category, None)

    def emit_app(
        self,
        session_id: UUID,
        event_name: str,
        payload: Mapping[str, Any],
        *,
        state_bearing: bool | None = None,
        agent_id: AgentId | None = None,
        correlation_id: UUID | None = None,
        idempotency_key: IdempotencyKey | None = None,
    ) -> int | None:
        try:
            return self._facade.emit_app(
                session_id,
                event_name,
                payload,
                state_bearing=state_bearing,
                agent_id=agent_id,
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
        except Exception as exc:
            return self._handle_error(exc, "emit_app", OperationCategory.TELEMETRY, None)

    def get_operational_scorecard(
        self,
        *,
        session_id: UUID | None = None,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> ControlPlaneScorecard | None:
        try:
            return self._facade.get_operational_scorecard(
                session_id=session_id,
                window_start=window_start,
                window_end=window_end,
            )
        except Exception as exc:
            return self._handle_error(exc, "get_operational_scorecard", OperationCategory.TELEMETRY, None)

    # ── Query (QUERY) ──────────────────────────────────────────────

    def get_session(self, session_id: UUID) -> SessionState | None:
        try:
            return self._facade.get_session(session_id)
        except Exception as exc:
            return self._handle_error(exc, "get_session", OperationCategory.QUERY, None)

    def replay(self, session_id: UUID, *, after_seq: int = 0, limit: int = 100) -> list[EventFrame]:
        try:
            return self._facade.replay(session_id, after_seq=after_seq, limit=limit)
        except Exception as exc:
            return self._handle_error(exc, "replay", OperationCategory.QUERY, [])

    def get_health_snapshot(self) -> SessionHealth | None:
        try:
            return self._facade.get_health_snapshot()
        except Exception as exc:
            return self._handle_error(exc, "get_health_snapshot", OperationCategory.QUERY, None)

    def get_ticket(self, ticket_id: UUID) -> ApprovalTicket | None:
        try:
            return self._facade.get_ticket(ticket_id)
        except Exception as exc:
            return self._handle_error(exc, "get_ticket", OperationCategory.QUERY, None)

    def list_tickets(
        self,
        *,
        session_id: UUID | None = None,
        statuses: list[ApprovalStatus] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[ApprovalTicket] | None:
        try:
            return self._facade.list_tickets(session_id=session_id, statuses=statuses, limit=limit, offset=offset)
        except Exception as exc:
            return self._handle_error(exc, "list_tickets", OperationCategory.QUERY, None)

    def get_proposal(self, proposal_id: UUID) -> ActionProposal | None:
        try:
            return self._facade.get_proposal(proposal_id)
        except Exception as exc:
            return self._handle_error(exc, "get_proposal", OperationCategory.QUERY, None)

    def list_proposals(
        self,
        *,
        session_id: UUID | None = None,
        statuses: list[ProposalStatus] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[ActionProposal] | None:
        try:
            return self._facade.list_proposals(session_id=session_id, statuses=statuses, limit=limit, offset=offset)
        except Exception as exc:
            return self._handle_error(exc, "list_proposals", OperationCategory.QUERY, None)

    def get_state_change_feed(
        self,
        *,
        session_id: UUID | None = None,
        cursor: int = 0,
        limit: int = 100,
    ) -> StateChangePage | None:
        try:
            return self._facade.get_state_change_feed(session_id=session_id, cursor=cursor, limit=limit)
        except Exception as exc:
            return self._handle_error(exc, "get_state_change_feed", OperationCategory.QUERY, None)

    def list_checkpoints(self, session_id: UUID, *, limit: int = 50, offset: int = 0) -> Page[SessionCheckpoint] | None:
        try:
            return self._facade.list_checkpoints(session_id, limit=limit, offset=offset)
        except Exception as exc:
            return self._handle_error(exc, "list_checkpoints", OperationCategory.QUERY, None)

    def get_plan_progress(self, session_id: UUID, goal_id: UUID) -> PlanProgress | None:
        try:
            return self._facade.get_plan_progress(session_id, goal_id)
        except Exception as exc:
            return self._handle_error(exc, "get_plan_progress", OperationCategory.QUERY, None)

    # ── Budget (BUDGET for checks, STATE_BEARING for mutations) ────

    def check_budget(self, session_id: UUID, *, cost: Decimal = Decimal("0"), action_count: int = 1) -> bool:
        try:
            return self._facade.check_budget(session_id, cost=cost, action_count=action_count)
        except Exception as exc:
            return self._handle_error(exc, "check_budget", OperationCategory.BUDGET, True)

    def increment_budget(self, session_id: UUID, *, cost: Decimal, action_count: int = 1) -> None:
        try:
            self._facade.increment_budget(session_id, cost=cost, action_count=action_count)
        except Exception as exc:
            self._handle_error(exc, "increment_budget", OperationCategory.STATE_BEARING, None)

    def get_remaining_budget(self, session_id: UUID) -> dict[str, Decimal | int] | None:
        try:
            return self._facade.get_remaining_budget(session_id)
        except Exception as exc:
            return self._handle_error(exc, "get_remaining_budget", OperationCategory.QUERY, None)

    # ── Proposals & approvals (STATE_BEARING) ──────────────────────

    def create_proposal(
        self,
        proposal: ActionProposal,
        *,
        command_id: IdempotencyKey | None = None,
    ) -> ActionProposal | None:
        try:
            return self._facade.create_proposal(proposal, command_id=command_id)
        except Exception as exc:
            return self._handle_error(exc, "create_proposal", OperationCategory.STATE_BEARING, None)

    def create_ticket(
        self,
        session_id: UUID,
        proposal_id: UUID,
        timeout_at: datetime,
        *,
        command_id: IdempotencyKey | None = None,
    ) -> ApprovalTicket | None:
        try:
            return self._facade.create_ticket(session_id, proposal_id, timeout_at, command_id=command_id)
        except Exception as exc:
            return self._handle_error(exc, "create_ticket", OperationCategory.STATE_BEARING, None)

    def approve_ticket(
        self,
        ticket_id: UUID,
        *,
        decided_by: str = "operator",
        reason: str | None = None,
        decision_type: ApprovalDecisionType = ApprovalDecisionType.ALLOW_ONCE,
        scope_resource_ids: list[str] | None = None,
        scope_max_cost: Decimal | None = None,
        scope_max_action_count: int | None = None,
        scope_expiry: datetime | None = None,
        command_id: IdempotencyKey | None = None,
    ) -> ApprovalTicket | None:
        try:
            return self._facade.approve_ticket(
                ticket_id,
                decided_by=decided_by,
                reason=reason,
                decision_type=decision_type,
                scope_resource_ids=scope_resource_ids,
                scope_max_cost=scope_max_cost,
                scope_max_action_count=scope_max_action_count,
                scope_expiry=scope_expiry,
                command_id=command_id,
            )
        except Exception as exc:
            return self._handle_error(exc, "approve_ticket", OperationCategory.STATE_BEARING, None)

    def deny_ticket(
        self,
        ticket_id: UUID,
        *,
        reason: str = "",
        command_id: IdempotencyKey | None = None,
    ) -> ApprovalTicket | None:
        try:
            return self._facade.deny_ticket(ticket_id, reason=reason, command_id=command_id)
        except Exception as exc:
            return self._handle_error(exc, "deny_ticket", OperationCategory.STATE_BEARING, None)

    # ── Kill switch (STATE_BEARING) ────────────────────────────────

    def kill_session(
        self,
        session_id: UUID,
        *,
        reason: str = "Kill switch triggered",
        command_id: IdempotencyKey | None = None,
    ) -> KillResult | None:
        try:
            return self._facade.kill_session(session_id, reason=reason, command_id=command_id)
        except Exception as exc:
            return self._handle_error(exc, "kill_session", OperationCategory.STATE_BEARING, None)

    def kill_system(
        self, *, reason: str = "System halt", command_id: IdempotencyKey | None = None
    ) -> KillResult | None:
        try:
            return self._facade.kill_system(reason=reason, command_id=command_id)
        except Exception as exc:
            return self._handle_error(exc, "kill_system", OperationCategory.STATE_BEARING, None)

    # ── Checkpoints & rollback (STATE_BEARING) ─────────────────────

    def create_checkpoint(
        self,
        session_id: UUID,
        *,
        label: str,
        metadata: dict[str, object] | None = None,
        created_by: str = "system",
        command_id: IdempotencyKey | None = None,
    ) -> SessionCheckpoint | None:
        try:
            return self._facade.create_checkpoint(
                session_id, label=label, metadata=metadata, created_by=created_by, command_id=command_id
            )
        except Exception as exc:
            return self._handle_error(exc, "create_checkpoint", OperationCategory.STATE_BEARING, None)

    def rollback_to_checkpoint(
        self,
        session_id: UUID,
        checkpoint_id: UUID,
        *,
        reason: str,
        command_id: IdempotencyKey | None = None,
    ) -> RollbackResult | None:
        try:
            return self._facade.rollback_to_checkpoint(session_id, checkpoint_id, reason=reason, command_id=command_id)
        except Exception as exc:
            return self._handle_error(exc, "rollback_to_checkpoint", OperationCategory.STATE_BEARING, None)

    # ── Goals & plans (TELEMETRY — informational, not state-bearing) ─

    def create_goal(
        self,
        session_id: UUID,
        *,
        name: str,
        description: str = "",
        metadata: dict[str, object] | None = None,
    ) -> Goal | None:
        try:
            return self._facade.create_goal(session_id, name=name, description=description, metadata=metadata)
        except Exception as exc:
            return self._handle_error(exc, "create_goal", OperationCategory.TELEMETRY, None)

    def create_plan(self, session_id: UUID, goal_id: UUID, *, title: str, steps: list[str]) -> Plan | None:
        try:
            return self._facade.create_plan(session_id, goal_id, title=title, steps=steps)
        except Exception as exc:
            return self._handle_error(exc, "create_plan", OperationCategory.TELEMETRY, None)

    def start_plan_step(self, session_id: UUID, plan_id: UUID, *, step_index: int) -> PlanStep | None:
        try:
            return self._facade.start_plan_step(session_id, plan_id, step_index=step_index)
        except Exception as exc:
            return self._handle_error(exc, "start_plan_step", OperationCategory.TELEMETRY, None)

    def complete_plan_step(
        self, session_id: UUID, plan_id: UUID, *, step_index: int, notes: str | None = None
    ) -> PlanStep | None:
        try:
            return self._facade.complete_plan_step(session_id, plan_id, step_index=step_index, notes=notes)
        except Exception as exc:
            return self._handle_error(exc, "complete_plan_step", OperationCategory.TELEMETRY, None)

    # ── Evaluations & guardrails (TELEMETRY) ───────────────────────

    def record_evaluation(
        self,
        session_id: UUID,
        *,
        operation: str,
        decision: EvaluationDecision,
        score: float,
        reasons: list[str],
        actions: list[str] | None = None,
    ) -> EvaluationResult | None:
        try:
            return self._facade.record_evaluation(
                session_id, operation=operation, decision=decision, score=score, reasons=reasons, actions=actions
            )
        except Exception as exc:
            return self._handle_error(exc, "record_evaluation", OperationCategory.TELEMETRY, None)

    def apply_guardrail(
        self,
        session_id: UUID,
        *,
        phase: GuardrailPhase,
        allow: bool,
        policy_code: str,
        reason: str,
        metadata: dict[str, object] | None = None,
    ) -> GuardrailDecision | None:
        try:
            return self._facade.apply_guardrail(
                session_id, phase=phase, allow=allow, policy_code=policy_code, reason=reason, metadata=metadata
            )
        except Exception as exc:
            return self._handle_error(exc, "apply_guardrail", OperationCategory.TELEMETRY, None)

    def request_handoff(
        self,
        session_id: UUID,
        *,
        source_agent_id: str,
        target_agent_id: str,
        allowed_actions: list[str],
        accepted: bool = True,
        lease_seconds: int = 900,
        metadata: dict[str, object] | None = None,
    ) -> HandoffResult | None:
        try:
            return self._facade.request_handoff(
                session_id,
                source_agent_id=source_agent_id,
                target_agent_id=target_agent_id,
                allowed_actions=allowed_actions,
                accepted=accepted,
                lease_seconds=lease_seconds,
                metadata=metadata,
            )
        except Exception as exc:
            return self._handle_error(exc, "request_handoff", OperationCategory.TELEMETRY, None)
