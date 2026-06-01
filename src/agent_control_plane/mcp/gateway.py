"""Embedded MCP tool-call governance gateway."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agent_control_plane.evaluators.protocol import ResponseEvaluator
    from agent_control_plane.types.enums import RiskLevel
    from agent_control_plane.types.steering import SteeringContext
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from agent_control_plane.engine.budget_tracker import BudgetExhaustedError
from agent_control_plane.engine.policy_engine import PolicyEngine
from agent_control_plane.models.registry import ModelRegistry
from agent_control_plane.storage.sqlalchemy_sync import SyncSqlAlchemyUnitOfWork
from agent_control_plane.sync import MappedEvent, SyncControlPlane
from agent_control_plane.types.enums import (
    ActionName,
    ActionTier,
    ActionValue,
    EventKind,
    McpEventName,
    ProposalStatus,
    SessionStatus,
    UnknownAppEventPolicy,
    parse_action_name,
)
from agent_control_plane.types.ids import AgentId, IdempotencyKey, ResourceId
from agent_control_plane.types.policies import PolicySnapshot
from agent_control_plane.types.proposals import ActionProposal

logger = logging.getLogger(__name__)


class ToolResultRejectedPayload(BaseModel):
    """Typed payload for TOOL_RESULT_REJECTED events.

    ``reason`` is a fixed literal rather than a free-text string: the evaluator's
    free-text reason may echo screened tool output, so only a sanitized category is
    persisted. See docs/security_model.md threat scenario 11.
    """

    tool_name: str
    evaluator: str
    reason: Literal["response_evaluator_denied"] = "response_evaluator_denied"


@dataclass
class _RejectionContext:
    evaluator: str
    reason: str


class McpGovernanceError(RuntimeError):
    """Base class for MCP governance failures."""


class PolicyDeniedError(McpGovernanceError):
    """Raised when policy denies a tool call."""


class ApprovalRequiredError(McpGovernanceError):
    """Raised when a tool call requires manual approval."""

    def __init__(self, message: str, *, ticket_id: UUID) -> None:
        super().__init__(message)
        self.ticket_id = ticket_id


class BudgetDeniedError(McpGovernanceError):
    """Raised when a tool call exceeds budget constraints."""


class KillSwitchActiveError(McpGovernanceError):
    """Raised when a session is not in an executable state."""


class SteeringRequiredError(McpGovernanceError):
    """Raised when a tool call is steered toward alternatives."""

    def __init__(self, message: str, *, steering: SteeringContext, reason: str) -> None:
        super().__init__(message)
        self.steering = steering
        self.reason = reason


class ToolExecutionError(McpGovernanceError):
    """Raised when the underlying tool execution fails."""


class ToolResultRejectedError(McpGovernanceError):
    """Raised when a response evaluator rejects tool output before it re-enters context.

    The tool ran and its cost is recorded, but the screened-out payload is withheld from
    the caller (fail-closed). ``evaluator`` names the evaluator that denied.
    """

    def __init__(self, message: str, *, evaluator: str) -> None:
        super().__init__(message)
        self.evaluator = evaluator


class ToolCallContext(BaseModel):
    """Normalized MCP tool-call request for governance and execution."""

    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    agent_id: str | None = None
    session_id: UUID | None = None
    correlation_id: UUID | None = None
    idempotency_key: str | None = None
    estimated_cost: Decimal = Decimal("0")
    model_id: str | None = None
    identity_user_id: str | None = None
    identity_org_id: str | None = None
    identity_team_id: str | None = None


class ToolCallResult(BaseModel):
    """Tool execution result with explicit budget cost attribution."""

    ok: bool
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    cost: Decimal = Decimal("0")
    input_tokens: int | None = None
    output_tokens: int | None = None
    model_id: str | None = None


@runtime_checkable
class ToolExecutor(Protocol):
    """Host-provided tool execution backend."""

    def execute(self, context: ToolCallContext) -> ToolCallResult: ...


class ToolPolicyMap:
    """Boundary mapper from MCP tool name to typed ActionName."""

    def __init__(self, mapping: Mapping[str, ActionName | str]) -> None:
        self._mapping = {k.strip().lower(): parse_action_name(v) for k, v in mapping.items()}

    def resolve(self, tool_name: str) -> ActionValue:
        return self._mapping.get(tool_name.strip().lower(), ActionName.UNKNOWN)


class McpGatewayConfig(BaseModel):
    """Configuration for the embedded MCP gateway."""

    policy_snapshot: PolicySnapshot = Field(default_factory=PolicySnapshot)
    auto_create_sessions: bool = True
    default_max_cost: Decimal = Decimal("10000")
    default_max_action_count: int = 100
    unknown_event_policy: UnknownAppEventPolicy = UnknownAppEventPolicy.RAISE


@dataclass(frozen=True)
class _EventSpec:
    event_kind: EventKind
    state_bearing: bool = False


class McpEventMapper:
    """Maps MCP app-events to control-plane EventKind values."""

    _MAPPING: ClassVar[dict[str, _EventSpec]] = {
        McpEventName.TOOL_CALL_RECEIVED.value: _EventSpec(EventKind.CYCLE_STARTED),
        McpEventName.TOOL_CALL_ALLOWED.value: _EventSpec(EventKind.RISK_ASSESSED),
        McpEventName.TOOL_CALL_BLOCKED.value: _EventSpec(EventKind.APPROVAL_DENIED),
        McpEventName.TOOL_CALL_APPROVAL_REQUIRED.value: _EventSpec(EventKind.APPROVAL_REQUESTED),
        McpEventName.TOOL_CALL_EXECUTED.value: _EventSpec(EventKind.EXECUTION_COMPLETED),
        McpEventName.TOOL_CALL_FAILED.value: _EventSpec(EventKind.EXECUTION_COMPLETED),
        McpEventName.TOOL_CALL_STEERED.value: _EventSpec(EventKind.RISK_ASSESSED),
        McpEventName.TOOL_RESULT_REJECTED.value: _EventSpec(EventKind.APPROVAL_DENIED, state_bearing=True),
    }

    def map_event(self, event_name: str, payload: Mapping[str, Any]) -> MappedEvent | None:
        spec = self._MAPPING.get(event_name.strip().lower())
        if spec is None:
            return None
        return MappedEvent(event_kind=spec.event_kind, payload=dict(payload), state_bearing=spec.state_bearing)


class McpGateway:
    """Govern MCP tool calls with policy, approval, budget, and audit events."""

    def __init__(
        self,
        control_plane: SyncControlPlane,
        executor: ToolExecutor,
        tool_policy_map: ToolPolicyMap,
        *,
        config: McpGatewayConfig | None = None,
        event_mapper: McpEventMapper | None = None,
        response_evaluators: Sequence[ResponseEvaluator] | None = None,
    ) -> None:
        self._cp = control_plane
        self._executor = executor
        self._tool_policy_map = tool_policy_map
        self._config = config or McpGatewayConfig()
        self._event_mapper = event_mapper or McpEventMapper()
        self._policy_engine = PolicyEngine(self._config.policy_snapshot)
        self._response_evaluators: tuple[ResponseEvaluator, ...] = tuple(response_evaluators or ())

    def handle_tool_call(self, context: ToolCallContext) -> ToolCallResult:
        """Govern and execute an MCP tool call."""
        session_id = self._resolve_session_id(context)
        self._assert_session_executable(session_id)

        action = self._tool_policy_map.resolve(context.tool_name)
        action_value = action.value if isinstance(action, ActionName) else action
        self._emit(
            session_id,
            McpEventName.TOOL_CALL_RECEIVED,
            {
                "tool_name": context.tool_name,
                "agent_id": context.agent_id,
                "action": action_value,
            },
            correlation_id=context.correlation_id,
            idempotency_key=context.idempotency_key,
        )

        if action == ActionName.UNKNOWN:
            self._emit(
                session_id,
                McpEventName.TOOL_CALL_BLOCKED,
                {"tool_name": context.tool_name, "reason": "unknown_tool"},
                correlation_id=context.correlation_id,
                idempotency_key=context.idempotency_key,
            )
            raise PolicyDeniedError(f"Unknown tool denied by fail-closed policy: {context.tool_name}")

        proposal = self._build_proposal(context, session_id, action)
        risk_level = self._policy_engine.classify_risk_level(proposal)
        tier = self._policy_engine.classify_action_tier(proposal, risk_level)
        reason, resolution = self._policy_engine.build_routing_reason(proposal, risk_level, tier)

        if tier == ActionTier.BLOCKED:
            self._emit(
                session_id,
                McpEventName.TOOL_CALL_BLOCKED,
                {
                    "tool_name": context.tool_name,
                    "reason": reason,
                    "resolution_step": resolution.value,
                },
                correlation_id=context.correlation_id,
                idempotency_key=context.idempotency_key,
            )
            raise PolicyDeniedError(reason)

        if tier == ActionTier.ALWAYS_APPROVE:
            ticket_id = self._create_approval_request(session_id, proposal)
            self._emit(
                session_id,
                McpEventName.TOOL_CALL_APPROVAL_REQUIRED,
                {
                    "tool_name": context.tool_name,
                    "ticket_id": str(ticket_id),
                    "reason": reason,
                },
                correlation_id=context.correlation_id,
                idempotency_key=context.idempotency_key,
            )
            raise ApprovalRequiredError("Manual approval required", ticket_id=ticket_id)

        if tier == ActionTier.STEER:
            steering_ctx = self._build_steering_context(proposal, risk_level)
            self._emit(
                session_id,
                McpEventName.TOOL_CALL_STEERED,
                {
                    "tool_name": context.tool_name,
                    "reason": reason,
                    "guidance": steering_ctx.guidance,
                    "suggested_actions": [str(a) for a in steering_ctx.suggested_actions],
                },
                correlation_id=context.correlation_id,
                idempotency_key=context.idempotency_key,
            )
            raise SteeringRequiredError("Action requires steering", steering=steering_ctx, reason=reason)

        if not self._cp.check_budget(session_id, cost=context.estimated_cost, action_count=1):
            self._emit(
                session_id,
                McpEventName.TOOL_CALL_BLOCKED,
                {
                    "tool_name": context.tool_name,
                    "reason": "budget_denied",
                    "estimated_cost": str(context.estimated_cost),
                },
                correlation_id=context.correlation_id,
                idempotency_key=context.idempotency_key,
            )
            raise BudgetDeniedError("Budget check failed")

        self._emit(
            session_id,
            McpEventName.TOOL_CALL_ALLOWED,
            {
                "tool_name": context.tool_name,
                "tier": tier.value,
                "risk_level": risk_level.value,
                "resolution_step": resolution.value,
            },
            correlation_id=context.correlation_id,
            idempotency_key=context.idempotency_key,
        )

        try:
            result = self._executor.execute(context)
        except Exception as exc:  # pragma: no cover - defensive conversion
            self._emit(
                session_id,
                McpEventName.TOOL_CALL_FAILED,
                {"tool_name": context.tool_name, "error": str(exc)},
                correlation_id=context.correlation_id,
                idempotency_key=context.idempotency_key,
            )
            raise ToolExecutionError(str(exc)) from exc

        if not result.ok:
            self._emit(
                session_id,
                McpEventName.TOOL_CALL_FAILED,
                {"tool_name": context.tool_name, "error": result.error},
                correlation_id=context.correlation_id,
                idempotency_key=context.idempotency_key,
            )
            raise ToolExecutionError(result.error or "Tool execution failed")

        try:
            self._cp.increment_budget(session_id, cost=result.cost, action_count=1)
        except BudgetExhaustedError as exc:
            self._emit(
                session_id,
                McpEventName.TOOL_CALL_BLOCKED,
                {
                    "tool_name": context.tool_name,
                    "reason": "budget_exhausted",
                    "cost": str(result.cost),
                },
                correlation_id=context.correlation_id,
                idempotency_key=context.idempotency_key,
            )
            raise BudgetDeniedError(str(exc)) from exc

        rejection = self._inspect_response(proposal, result)
        if rejection is not None:
            # The evaluator's free-text reason may be derived from the screened output, so it
            # is kept out of the persisted event and the caller-facing error; only the evaluator
            # name and a sanitized category are recorded. See docs/security_model.md threat 11.
            logger.warning("Tool output rejected by response evaluator %r: %s", rejection.evaluator, rejection.reason)
            self._emit(
                session_id,
                McpEventName.TOOL_RESULT_REJECTED,
                ToolResultRejectedPayload(
                    tool_name=context.tool_name,
                    evaluator=rejection.evaluator,
                ).model_dump(),
                correlation_id=context.correlation_id,
                idempotency_key=context.idempotency_key,
            )
            raise ToolResultRejectedError(
                f"Tool output rejected by response evaluator: {rejection.evaluator}",
                evaluator=rejection.evaluator,
            )

        self._emit(
            session_id,
            McpEventName.TOOL_CALL_EXECUTED,
            {
                "tool_name": context.tool_name,
                "cost": str(result.cost),
                "output_keys": sorted(result.output.keys()),
            },
            correlation_id=context.correlation_id,
            idempotency_key=context.idempotency_key,
        )
        return result

    def _inspect_response(self, proposal: ActionProposal, result: ToolCallResult) -> _RejectionContext | None:
        """Screen tool output through response evaluators. Returns a denial context on deny.

        Fail-closed: an evaluator that raises is treated as a denial. Cost is recorded by the
        caller regardless — the tool already ran — but the screened payload never reaches the
        caller when a denial is returned here.
        """
        for evaluator in self._response_evaluators:
            try:
                outcome = evaluator.evaluate_response(proposal, result.output, self._config.policy_snapshot)
            except Exception as exc:  # fail-closed on evaluator error
                logger.warning("Response evaluator %r raised (fail-closed): %s", evaluator.name, exc)
                return _RejectionContext(evaluator=evaluator.name, reason=f"Response evaluator error: {exc}")
            if not outcome.allow:
                return _RejectionContext(evaluator=evaluator.name, reason=outcome.reason)
        return None

    def _resolve_session_id(self, context: ToolCallContext) -> UUID:
        if context.session_id is not None:
            return context.session_id
        if not self._config.auto_create_sessions:
            raise PolicyDeniedError("session_id is required when auto_create_sessions is disabled")
        session_name = f"mcp-{context.agent_id or 'agent'}-{context.tool_name}"
        return self._cp.create_session(
            name=session_name,
            max_cost=self._config.default_max_cost,
            max_action_count=self._config.default_max_action_count,
            execution_mode=self._config.policy_snapshot.execution_mode,
        )

    def _assert_session_executable(self, session_id: UUID) -> None:
        state = self._cp.get_session(session_id)
        if state is None:
            raise PolicyDeniedError(f"Session not found: {session_id}")
        if state.status in {SessionStatus.ABORTED, SessionStatus.COMPLETED, SessionStatus.PAUSED}:
            raise KillSwitchActiveError(f"Session is not executable: {state.status.value}")

    def _build_proposal(self, context: ToolCallContext, session_id: UUID, action: ActionValue) -> ActionProposal:
        resource_id = str(context.arguments.get("resource_id") or context.arguments.get("id") or context.tool_name)
        resource_type = str(context.arguments.get("resource_type") or "mcp_tool")
        raw_score = context.arguments.get("score", "1.0")
        score = Decimal(str(raw_score))
        return ActionProposal(
            session_id=session_id,
            agent_id=AgentId(context.agent_id) if context.agent_id is not None else None,
            resource_id=ResourceId(resource_id),
            resource_type=resource_type,
            decision=action,
            reasoning=f"MCP tool call: {context.tool_name}",
            metadata={"tool_name": context.tool_name},
            weight=context.estimated_cost,
            score=score,
        )

    def _create_approval_request(self, session_id: UUID, proposal: ActionProposal) -> UUID:
        timeout_at = datetime.now(UTC) + timedelta(seconds=self._config.policy_snapshot.approval_timeout_seconds)
        with self._cp.session_scope() as db:
            uow = SyncSqlAlchemyUnitOfWork(db)
            proposal_id = self._insert_proposal_row(db, proposal)
            ticket = uow.approval_repo.create_ticket(session_id, proposal_id, timeout_at)
            uow.event_repo.append(
                session_id=session_id,
                event_kind=EventKind.APPROVAL_REQUESTED,
                payload={
                    "ticket_id": str(ticket.id),
                    "proposal_id": str(proposal_id),
                    "tool_name": proposal.metadata.get("tool_name"),
                },
                state_bearing=True,
            )
            uow.commit()
            return ticket.id

    def _insert_proposal_row(self, db: Session, proposal: ActionProposal) -> UUID:
        action_proposal_model = ModelRegistry.get("ActionProposal")
        row = action_proposal_model(
            id=proposal.id,
            session_id=proposal.session_id,
            cycle_event_seq=proposal.cycle_event_seq,
            resource_id=proposal.resource_id,
            resource_type=proposal.resource_type,
            decision=proposal.decision,
            reasoning=proposal.reasoning,
            metadata_json=proposal.metadata,
            weight=proposal.weight,
            score=proposal.score,
            action_tier=proposal.action_tier,
            risk_level=proposal.risk_level,
            status=ProposalStatus.PENDING,
            created_at=proposal.created_at,
        )
        db.add(row)
        db.flush()
        return proposal.id

    def _build_steering_context(self, proposal: ActionProposal, risk_level: RiskLevel) -> SteeringContext:
        from agent_control_plane.engine.action_policy import SteeringActionHandler
        from agent_control_plane.types.steering import SteeringContext

        handler = self._policy_engine.get_action_handler(proposal)
        if isinstance(handler, SteeringActionHandler):
            return handler.build_steering_context(proposal, risk_level, self._policy_engine.policy)
        return SteeringContext(guidance="Action requires steering", suggested_actions=[])

    def _emit(
        self,
        session_id: UUID,
        event_name: McpEventName,
        payload: dict[str, Any],
        *,
        correlation_id: UUID | None = None,
        idempotency_key: str | None = None,
        state_bearing: bool | None = None,
    ) -> int | None:
        return self._cp.emit_app_event(
            session_id=session_id,
            event_name=event_name.value,
            payload=payload,
            mapper=self._event_mapper,
            unknown_policy=self._config.unknown_event_policy,
            state_bearing=state_bearing,
            correlation_id=correlation_id,
            idempotency_key=IdempotencyKey(idempotency_key) if idempotency_key is not None else None,
        )
