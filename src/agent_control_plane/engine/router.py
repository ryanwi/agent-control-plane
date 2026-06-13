"""Deterministic proposal routing to action tiers."""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from agent_control_plane.engine.policy_engine import PolicyEngine
from agent_control_plane.types.enums import ActionTier, EventKind, RiskLevel, RoutingResolutionStep, SessionStatus
from agent_control_plane.types.proposals import ActionProposal
from agent_control_plane.types.risk import SessionRiskEscalation
from agent_control_plane.types.sessions import SessionState
from agent_control_plane.types.steering import SteeringContext

if TYPE_CHECKING:
    from agent_control_plane.engine.agent_registry import AgentRegistry
    from agent_control_plane.engine.event_store import EventStore
    from agent_control_plane.engine.session_risk_accumulator import SessionRiskAccumulator

logger = logging.getLogger(__name__)


@dataclass
class RoutingDecision:
    """Result of routing a proposal through the policy engine."""

    tier: ActionTier
    risk_level: RiskLevel
    reason: str
    resolution_step: RoutingResolutionStep
    steering: SteeringContext | None = field(default=None)
    risk_escalated: bool = False
    risk_escalation: SessionRiskEscalation | None = None


class ProposalRouter:
    """Routes proposals through the policy engine with full audit trail."""

    def __init__(
        self,
        policy_engine: PolicyEngine,
        *,
        agent_registry: AgentRegistry | None = None,
        risk_accumulator: SessionRiskAccumulator | None = None,
        event_store: EventStore | None = None,
        session_repository: Any = None,
    ) -> None:
        self.policy_engine = policy_engine
        self.agent_registry = agent_registry
        self.risk_accumulator = risk_accumulator
        self.event_store = event_store
        self.session_repository = session_repository
        self._session_repo_is_async: bool = session_repository is not None and inspect.iscoroutinefunction(
            getattr(session_repository, "get_session", None)
        )

    async def _check_agent_registration(self, proposal: ActionProposal) -> RoutingDecision | None:
        if self.agent_registry and proposal.agent_id:
            agent = await self.agent_registry.get_agent(proposal.agent_id)
            if not agent:
                logger.warning("Proposal from unregistered agent: %s", proposal.agent_id)
            elif await self.agent_registry.is_revoked(proposal.session_id, proposal.agent_id):
                # Per-session revocation fails closed, regardless of the agent's capabilities.
                logger.warning("Agent %s is revoked for session %s — blocking", proposal.agent_id, proposal.session_id)
                return RoutingDecision(
                    tier=ActionTier.BLOCKED,
                    risk_level=self.policy_engine.classify_risk_level(proposal),
                    reason=f"Agent {proposal.agent_id} is revoked for session {proposal.session_id}",
                    resolution_step=RoutingResolutionStep.CAPABILITY_MATCH,
                    steering=None,
                )
            else:
                # Validate against the agent's own capabilities only.
                capable = agent.is_capable(proposal.decision)
                if not capable:
                    logger.warning(
                        "Agent %s is not registered for action %s",
                        proposal.agent_id,
                        proposal.decision,
                    )
        return None

    async def _resolve_session_state(
        self,
        proposal: ActionProposal,
        session_state: SessionState | None = None,
    ) -> SessionState | None:
        if session_state is not None:
            return session_state
        if self.session_repository is not None and proposal.session_id is not None:
            if self._session_repo_is_async:
                return cast(SessionState | None, await self.session_repository.get_session(proposal.session_id))
            return cast(SessionState | None, self.session_repository.get_session(proposal.session_id))
        return None

    def _get_clarification_fields(self, proposal: ActionProposal) -> list[str]:
        meta = proposal.metadata
        if not meta:
            return []
        seen: dict[str, None] = {}
        if "missing_fields" in meta and isinstance(meta["missing_fields"], list):
            seen.update(dict.fromkeys(meta["missing_fields"]))
        if "ambiguous_fields" in meta and isinstance(meta["ambiguous_fields"], list):
            seen.update(dict.fromkeys(meta["ambiguous_fields"]))
        for k, v in meta.items():
            if v == "Ambiguous":
                seen[k] = None
        return list(seen)

    async def _check_clarification(
        self,
        proposal: ActionProposal,
        original_risk_level: RiskLevel,
        session_state: SessionState | None = None,
    ) -> RoutingDecision | None:
        clarification_fields = self._get_clarification_fields(proposal)
        if not clarification_fields:
            return None

        resolved_state = await self._resolve_session_state(proposal, session_state)
        if resolved_state is not None:
            resolved_state.status = SessionStatus.SUSPENDED_FOR_CLARIFICATION
            if self.session_repository is not None and proposal.session_id is not None:
                if self._session_repo_is_async:
                    await self.session_repository.update_session(
                        proposal.session_id, status=SessionStatus.SUSPENDED_FOR_CLARIFICATION
                    )
                else:
                    self.session_repository.update_session(
                        proposal.session_id, status=SessionStatus.SUSPENDED_FOR_CLARIFICATION
                    )

        if self.event_store is not None and proposal.session_id is not None:
            await self.event_store.append(
                session_id=proposal.session_id,
                event_kind=EventKind.CLARIFICATION_REQUESTED,
                payload={
                    "proposal_id": str(proposal.id),
                    "decision": proposal.decision,
                    "required_fields": clarification_fields,
                },
                state_bearing=True,
            )

        return RoutingDecision(
            tier=ActionTier.CLARIFY,
            risk_level=original_risk_level,
            reason=f"Proposal requires clarification for fields: {clarification_fields}",
            resolution_step=RoutingResolutionStep.RISK_TIER_MATCH,
            steering=None,
        )

    async def _check_steering_limit(
        self,
        proposal: ActionProposal,
        session_state: SessionState | None = None,
    ) -> RoutingDecision | None:
        resolved_state = await self._resolve_session_state(proposal, session_state)
        max_retries = getattr(self.policy_engine.policy, "max_steering_retries", 3)
        steer_count = 0
        if resolved_state is not None:
            steer_count = resolved_state.steering_history.get(proposal.decision, 0)

        if steer_count >= max_retries:
            if self.event_store is not None and proposal.session_id is not None:
                await self.event_store.append(
                    session_id=proposal.session_id,
                    event_kind=EventKind.STEERING_LIMIT_EXCEEDED,
                    payload={
                        "proposal_id": str(proposal.id),
                        "decision": proposal.decision,
                        "steer_count": steer_count,
                        "max_retries": max_retries,
                        "outcome": "escalated_to_approval",
                    },
                    state_bearing=True,
                )
            return RoutingDecision(
                tier=ActionTier.ALWAYS_APPROVE,
                risk_level=RiskLevel.HIGH,
                reason=f"Steering limit exceeded ({steer_count}/{max_retries}) for action {proposal.decision}",
                resolution_step=RoutingResolutionStep.RISK_TIER_MATCH,
                steering=None,
            )

        if resolved_state is not None:
            resolved_state.steering_history[proposal.decision] = steer_count + 1
            if self.session_repository is not None and proposal.session_id is not None:
                if self._session_repo_is_async:
                    await self.session_repository.update_session(
                        proposal.session_id, steering_history=resolved_state.steering_history
                    )
                else:
                    self.session_repository.update_session(
                        proposal.session_id, steering_history=resolved_state.steering_history
                    )
        return None

    async def route(
        self,
        proposal: ActionProposal,
        session_state: SessionState | None = None,
    ) -> RoutingDecision:
        """Route a proposal and return the decision with audit trail."""
        agent_decision = await self._check_agent_registration(proposal)
        if agent_decision is not None:
            return agent_decision

        resolved_state = await self._resolve_session_state(proposal, session_state)
        original_risk_level = self.policy_engine.classify_risk_level(proposal)

        clarify_decision = await self._check_clarification(proposal, original_risk_level, resolved_state)
        if clarify_decision is not None:
            return clarify_decision

        risk_escalation = None
        risk_level = original_risk_level
        if self.risk_accumulator is not None:
            risk_escalation = await self.risk_accumulator.assess(proposal.session_id, proposal, original_risk_level)
            risk_level = risk_escalation.escalated_risk

        tier = self.policy_engine.classify_action_tier(
            proposal,
            risk_level,
            can_auto_approve=await self.policy_engine.can_auto_approve_with_tree(proposal, risk_level),
        )

        if tier == ActionTier.STEER:
            steer_decision = await self._check_steering_limit(proposal, resolved_state)
            if steer_decision is not None:
                return steer_decision

        routing = self.policy_engine.build_routing_reason(proposal, risk_level, tier)

        steering = None
        if tier == ActionTier.STEER:
            from agent_control_plane.engine.action_policy import SteeringActionHandler

            h = self.policy_engine.get_action_handler(proposal)
            if isinstance(h, SteeringActionHandler):
                steering = h.build_steering_context(proposal, risk_level, self.policy_engine.policy)

        logger.info(
            "Routed proposal %s -> %s (risk=%s, step=%s)",
            proposal.id,
            tier.value,
            risk_level.value,
            routing.resolution_step,
        )

        return RoutingDecision(
            tier=tier,
            risk_level=risk_level,
            reason=routing.reason,
            resolution_step=routing.resolution_step,
            steering=steering,
            risk_escalated=risk_escalation.was_escalated if risk_escalation is not None else False,
            risk_escalation=risk_escalation,
        )
