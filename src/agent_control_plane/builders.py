"""Lightweight composition helpers for partial integrations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from agent_control_plane.engine.budget_tracker import BudgetTracker
from agent_control_plane.engine.event_store import EventStore
from agent_control_plane.engine.kill_switch import KillSwitch
from agent_control_plane.engine.session_manager import SessionManager
from agent_control_plane.storage.protocols import (
    AsyncApprovalRepository,
    AsyncEventRepository,
    AsyncSessionRepository,
)
from agent_control_plane.types.enums import ProposalStatus


@dataclass
class SessionEventBudgetServices:
    session_manager: SessionManager
    event_store: EventStore
    budget_tracker: BudgetTracker


@dataclass
class KillSwitchServices:
    session_manager: SessionManager
    event_store: EventStore
    kill_switch: KillSwitch


class AsyncProposalRepositoryNoop(Protocol):
    async def update_status(self, proposal_id: UUID, status: ProposalStatus) -> None: ...
    async def has_pending_for_resource(self, session_id: UUID, resource_id: str) -> bool: ...


def build_session_event_budget(
    *,
    session_repo: AsyncSessionRepository,
    event_repo: AsyncEventRepository,
) -> SessionEventBudgetServices:
    """Build only session/event/budget services for minimal integrations."""
    return SessionEventBudgetServices(
        session_manager=SessionManager(session_repo),
        event_store=EventStore(event_repo),
        budget_tracker=BudgetTracker(session_repo),
    )


def build_kill_switch_stack(
    *,
    session_repo: AsyncSessionRepository,
    event_repo: AsyncEventRepository,
    approval_repo: AsyncApprovalRepository,
) -> KillSwitchServices:
    """Build kill-switch stack without requiring proposal/approval gate wiring."""
    session_manager = SessionManager(session_repo)
    event_store = EventStore(event_repo)
    kill_switch = KillSwitch(session_manager, event_store, session_repo, approval_repo)
    return KillSwitchServices(
        session_manager=session_manager,
        event_store=event_store,
        kill_switch=kill_switch,
    )
