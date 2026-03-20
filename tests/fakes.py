"""In-memory repository fakes for testing.

Dict/list-backed, no SQLAlchemy dependency. Returns real DTOs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from agent_control_plane.engine.budget_tracker import BudgetExhaustedError
from agent_control_plane.types.agents import AgentMetadata, DelegationProposal
from agent_control_plane.types.approvals import ApprovalTicket
from agent_control_plane.types.enums import (
    ApprovalDecisionType,
    ApprovalStatus,
    BudgetPeriod,
    EventKind,
    ProposalStatus,
    SessionStatus,
)
from agent_control_plane.types.frames import EventFrame
from agent_control_plane.types.proposals import ActionProposal
from agent_control_plane.types.sessions import BudgetInfo, SessionState
from agent_control_plane.types.token_governance import (
    IdentityContext,
    TokenBudgetConfig,
    TokenBudgetState,
    TokenUsage,
    TokenUsageSummary,
)


class InMemorySessionRepository:
    """In-memory session repository for tests."""

    def __init__(self) -> None:
        self._sessions: dict[UUID, SessionState] = {}
        self._seq_counters: dict[UUID, int] = {}
        self._policies: dict[UUID, dict] = {}

    async def get_session(self, session_id: UUID) -> SessionState | None:
        return self._sessions.get(session_id)

    async def get_session_for_update(self, session_id: UUID) -> SessionState:
        cs = self._sessions.get(session_id)
        if cs is None:
            raise ValueError(f"Session {session_id} not found")
        return cs

    async def create_session(self, **kwargs: Any) -> SessionState:
        sid = uuid4()
        cs = SessionState(id=sid, **kwargs)
        self._sessions[sid] = cs
        return cs

    async def update_session(self, session_id: UUID, **fields: Any) -> None:
        cs = self._sessions.get(session_id)
        if cs is None:
            raise ValueError(f"Session {session_id} not found")
        for k, v in fields.items():
            if hasattr(cs, k):
                object.__setattr__(cs, k, v)

    async def set_active_cycle(self, session_id: UUID, cycle_id: UUID | None) -> None:
        cs = self._sessions.get(session_id)
        if cs is None:
            raise ValueError(f"Session {session_id} not found")
        object.__setattr__(cs, "active_cycle_id", cycle_id)

    async def list_sessions(self, statuses: list[SessionStatus] | None = None, limit: int = 50) -> list[SessionState]:
        result = list(self._sessions.values())
        if statuses:
            result = [s for s in result if s.status in statuses]
        return result[:limit]

    async def increment_budget(self, session_id: UUID, cost: Decimal, action_count: int) -> None:
        cs = self._sessions.get(session_id)
        if cs is None:
            raise ValueError(f"Session {session_id} not found")
        new_cost = cs.used_cost + cost
        new_count = cs.used_action_count + action_count
        if new_cost > cs.max_cost:
            raise BudgetExhaustedError(f"Cost budget exceeded: {new_cost} > {cs.max_cost}")
        if new_count > cs.max_action_count:
            raise BudgetExhaustedError(f"Action count exceeded: {new_count} > {cs.max_action_count}")
        object.__setattr__(cs, "used_cost", new_cost)
        object.__setattr__(cs, "used_action_count", new_count)

    async def get_budget(self, session_id: UUID) -> BudgetInfo:
        cs = self._sessions.get(session_id)
        if cs is None:
            raise ValueError(f"Session {session_id} not found")
        return BudgetInfo(
            remaining_cost=cs.max_cost - cs.used_cost,
            remaining_count=cs.max_action_count - cs.used_action_count,
            used_cost=cs.used_cost,
            used_count=cs.used_action_count,
            max_cost=cs.max_cost,
            max_count=cs.max_action_count,
        )

    async def create_policy(self, **kwargs: Any) -> UUID:
        pid = uuid4()
        self._policies[pid] = kwargs
        return pid

    async def create_seq_counter(self, session_id: UUID) -> None:
        self._seq_counters[session_id] = 1


class InMemoryEventRepository:
    """In-memory event repository for tests."""

    def __init__(self, *, fail: bool = False) -> None:
        self._events: dict[UUID, list[EventFrame]] = {}
        self._seq_counters: dict[UUID, int] = {}
        self._fail = fail

    async def append(
        self,
        session_id: UUID,
        event_kind: EventKind,
        payload: dict[str, Any],
        *,
        state_bearing: bool = False,
        agent_id: str | None = None,
        correlation_id: UUID | None = None,
        routing_decision: dict[str, Any] | None = None,
        routing_reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> int:
        if self._fail:
            raise RuntimeError("event repo failure")
        seq = self._seq_counters.get(session_id, 1)
        self._seq_counters[session_id] = seq + 1
        event = EventFrame(
            session_id=session_id,
            seq=seq,
            event_kind=event_kind,
            agent_id=agent_id,
            correlation_id=correlation_id,
            payload=payload,
            routing_decision=routing_decision,
            routing_reason=routing_reason,
        )
        self._events.setdefault(session_id, []).append(event)
        return seq

    async def replay(self, session_id: UUID, after_seq: int = 0, limit: int = 100) -> list[EventFrame]:
        events = self._events.get(session_id, [])
        return [e for e in events if e.seq > after_seq][:limit]

    async def get_last_event(self, session_id: UUID) -> EventFrame | None:
        events = self._events.get(session_id, [])
        return events[-1] if events else None


class InMemoryApprovalRepository:
    """In-memory approval repository for tests."""

    def __init__(self) -> None:
        self._tickets: dict[UUID, ApprovalTicket] = {}

    async def create_ticket(self, session_id: UUID, proposal_id: UUID, timeout_at: datetime) -> ApprovalTicket:
        ticket = ApprovalTicket(
            id=uuid4(),
            session_id=session_id,
            proposal_id=proposal_id,
            status=ApprovalStatus.PENDING,
            timeout_at=timeout_at,
        )
        self._tickets[ticket.id] = ticket
        return ticket

    async def get_pending_ticket_for_update(self, ticket_id: UUID) -> ApprovalTicket:
        ticket = self._tickets.get(ticket_id)
        if ticket is None:
            raise ValueError(f"Ticket {ticket_id} not found")
        if ticket.status != ApprovalStatus.PENDING:
            raise ValueError(f"Ticket {ticket_id} is not pending (status={ticket.status})")
        return ticket

    async def update_ticket(self, ticket_id: UUID, **fields: Any) -> None:
        ticket = self._tickets.get(ticket_id)
        if ticket is None:
            raise ValueError(f"Ticket {ticket_id} not found")
        for k, v in fields.items():
            if hasattr(ticket, k):
                object.__setattr__(ticket, k, v)

    async def get_pending_tickets(self, session_id: UUID | None = None) -> list[ApprovalTicket]:
        result = [t for t in self._tickets.values() if t.status == ApprovalStatus.PENDING]
        if session_id is not None:
            result = [t for t in result if t.session_id == session_id]
        return result

    async def get_session_scope_tickets(self, session_id: UUID) -> list[ApprovalTicket]:
        return [
            t
            for t in self._tickets.values()
            if t.session_id == session_id
            and t.status == ApprovalStatus.APPROVED
            and t.decision_type == ApprovalDecisionType.ALLOW_FOR_SESSION
        ]

    async def decrement_scope_count(self, ticket_id: UUID) -> None:
        ticket = self._tickets.get(ticket_id)
        if ticket is not None and ticket.scope_max_count is not None:
            object.__setattr__(ticket, "scope_max_count", ticket.scope_max_count - 1)

    async def deny_all_pending(self, session_id: UUID) -> int:
        count = 0
        for ticket in self._tickets.values():
            if ticket.session_id == session_id and ticket.status == ApprovalStatus.PENDING:
                object.__setattr__(ticket, "status", ApprovalStatus.DENIED)
                object.__setattr__(ticket, "decision_reason", "Kill switch triggered")
                count += 1
        return count

    async def expire_timed_out(self) -> list[ApprovalTicket]:
        now = datetime.now(UTC)
        expired = []
        for ticket in self._tickets.values():
            if ticket.status == ApprovalStatus.PENDING and ticket.timeout_at and ticket.timeout_at <= now:
                object.__setattr__(ticket, "status", ApprovalStatus.EXPIRED)
                object.__setattr__(ticket, "decided_at", now)
                object.__setattr__(ticket, "decision_reason", "Timeout expired (safe default: deny)")
                expired.append(ticket)
        return expired


class InMemoryProposalRepository:
    """In-memory proposal repository for tests."""

    def __init__(self) -> None:
        self._proposals: dict[UUID, ActionProposal] = {}

    def add_proposal(
        self,
        proposal_id: UUID,
        session_id: UUID,
        resource_id: str,
        status: ProposalStatus = ProposalStatus.PENDING,
    ) -> None:
        self._proposals[proposal_id] = ActionProposal(
            id=proposal_id,
            session_id=session_id,
            resource_id=resource_id,
            resource_type="task",
            decision="status",
            reasoning="test",
            status=status,
        )

    async def create_proposal(self, proposal: ActionProposal) -> ActionProposal:
        self._proposals[proposal.id] = proposal
        return proposal

    async def get_proposal(self, proposal_id: UUID) -> ActionProposal | None:
        return self._proposals.get(proposal_id)

    async def list_proposals(
        self,
        *,
        session_id: UUID | None = None,
        statuses: list[ProposalStatus] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ActionProposal]:
        rows = list(self._proposals.values())
        if session_id is not None:
            rows = [row for row in rows if row.session_id == session_id]
        if statuses:
            rows = [row for row in rows if row.status in statuses]
        return rows[offset : offset + limit]

    async def update_status(self, proposal_id: UUID, status: ProposalStatus) -> None:
        if proposal_id in self._proposals:
            self._proposals[proposal_id] = self._proposals[proposal_id].model_copy(update={"status": status})

    async def has_pending_for_resource(self, session_id: UUID, resource_id: str) -> bool:
        return any(
            p.session_id == session_id and p.resource_id == resource_id and p.status == ProposalStatus.PENDING
            for p in self._proposals.values()
        )


class InMemoryAgentRepository:
    """In-memory agent repository for tests."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentMetadata] = {}
        self._delegations: list[DelegationProposal] = []

    async def register_agent(self, agent: AgentMetadata) -> None:
        self._agents[agent.id] = agent

    async def get_agent(self, agent_id: str) -> AgentMetadata | None:
        return self._agents.get(agent_id)

    async def list_agents(self, tags: list[str] | None = None) -> list[AgentMetadata]:
        agents = list(self._agents.values())
        if tags:
            return [a for a in agents if any(t in a.tags for t in tags)]
        return agents

    async def record_delegation(self, delegation: DelegationProposal) -> None:
        self._delegations.append(delegation)


class InMemoryTokenBudgetRepository:
    """In-memory token budget repository for tests."""

    def __init__(self) -> None:
        self._configs: dict[UUID, TokenBudgetConfig] = {}
        self._states: dict[tuple[UUID, datetime], TokenBudgetState] = {}
        self._usage_records: list[dict[str, Any]] = []

    async def get_budget_config(self, config_id: UUID) -> TokenBudgetConfig | None:
        return self._configs.get(config_id)

    async def list_budget_configs(self, identity: IdentityContext) -> list[TokenBudgetConfig]:
        results: list[TokenBudgetConfig] = []
        for config in self._configs.values():
            ci = config.identity
            if ci.user_id is not None and ci.user_id != identity.user_id:
                continue
            if ci.org_id is not None and ci.org_id != identity.org_id:
                continue
            if ci.team_id is not None and ci.team_id != identity.team_id:
                continue
            results.append(config)
        return results

    async def create_budget_config(self, config: TokenBudgetConfig) -> TokenBudgetConfig:
        self._configs[config.id] = config
        return config

    async def get_budget_state(self, config_id: UUID, window_start: datetime) -> TokenBudgetState | None:
        return self._states.get((config_id, window_start))

    async def increment_usage(
        self,
        config_id: UUID,
        window_start: datetime,
        window_end: datetime,
        tokens: int,
        cost_usd: Decimal,
    ) -> TokenBudgetState:
        key = (config_id, window_start)
        config = self._configs.get(config_id)
        if config is None:
            raise ValueError(f"Config {config_id} not found")
        existing = self._states.get(key)
        if existing is not None:
            new_tokens = existing.used_tokens + tokens
            new_cost = existing.used_cost_usd + cost_usd
            remaining_tokens = (config.max_tokens - new_tokens) if config.max_tokens is not None else None
            remaining_cost = (config.max_cost_usd - new_cost) if config.max_cost_usd is not None else None
            state = TokenBudgetState(
                config_id=config_id,
                identity=config.identity,
                period=config.period,
                window_start=window_start,
                window_end=window_end,
                used_tokens=new_tokens,
                used_cost_usd=new_cost,
                remaining_tokens=remaining_tokens,
                remaining_cost_usd=remaining_cost,
            )
        else:
            remaining_tokens = (config.max_tokens - tokens) if config.max_tokens is not None else None
            remaining_cost = (config.max_cost_usd - cost_usd) if config.max_cost_usd is not None else None
            state = TokenBudgetState(
                config_id=config_id,
                identity=config.identity,
                period=config.period,
                window_start=window_start,
                window_end=window_end,
                used_tokens=tokens,
                used_cost_usd=cost_usd,
                remaining_tokens=remaining_tokens,
                remaining_cost_usd=remaining_cost,
            )
        self._states[key] = state
        return state

    async def record_usage(self, session_id: UUID, usage: TokenUsage, identity: IdentityContext) -> None:
        self._usage_records.append(
            {
                "session_id": session_id,
                "usage": usage,
                "identity": identity,
                "created_at": datetime.now(UTC),
            }
        )

    async def get_usage_summary(
        self, identity: IdentityContext, period: BudgetPeriod, window_start: datetime
    ) -> TokenUsageSummary | None:
        matching = [r for r in self._usage_records if r["identity"] == identity and r["created_at"] >= window_start]
        if not matching:
            return None
        total_input = sum(r["usage"].input_tokens for r in matching)
        total_output = sum(r["usage"].output_tokens for r in matching)
        total = sum(r["usage"].total_tokens for r in matching)
        total_cost = sum((r["usage"].estimated_cost_usd for r in matching), Decimal("0"))
        model_breakdown: dict[str, int] = {}
        for r in matching:
            mid = str(r["usage"].model_id)
            model_breakdown[mid] = model_breakdown.get(mid, 0) + r["usage"].total_tokens
        return TokenUsageSummary(
            identity=identity,
            period=period,
            window_start=window_start,
            window_end=datetime.now(UTC),
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            total_tokens=total,
            total_cost_usd=total_cost,
            model_breakdown=model_breakdown,
            action_count=len(matching),
        )
