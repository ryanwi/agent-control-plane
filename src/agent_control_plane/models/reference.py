"""Reference ORM models for the control plane.

These concrete models compose the mixins with a shared Base class and add
primary keys, foreign keys, and table names. Host applications that don't
need custom model layout can use these directly:

    from agent_control_plane.models.reference import Base, register_models, create_tables
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import VARCHAR, ForeignKey, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql.sqltypes import Uuid

from agent_control_plane.models.mixins import (
    ActionProposalMixin,
    AgentMixin,
    ApprovalTicketMixin,
    CommandLedgerMixin,
    ControlEventMixin,
    ControlSessionMixin,
    DelegationMixin,
    PolicySnapshotMixin,
    SessionSeqCounterMixin,
)
from agent_control_plane.models.registry import DEFAULT_MODEL_REGISTRY, RegistryProtocol


class Base(DeclarativeBase):
    """Shared declarative base for reference models."""


class PolicySnapshot(Base, PolicySnapshotMixin):
    __tablename__ = "policy_snapshots"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)


class ControlSession(Base, ControlSessionMixin):
    __tablename__ = "control_sessions"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)


class SessionSeqCounter(Base, SessionSeqCounterMixin):
    __tablename__ = "session_seq_counters"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("control_sessions.id"), nullable=False)


class ControlEvent(Base, ControlEventMixin):
    __tablename__ = "control_events"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("control_sessions.id"), nullable=False)


class ActionProposal(Base, ActionProposalMixin):
    __tablename__ = "action_proposals"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("control_sessions.id"), nullable=False)


class ApprovalTicket(Base, ApprovalTicketMixin):
    __tablename__ = "approval_tickets"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("control_sessions.id"), nullable=False)
    proposal_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("action_proposals.id"), nullable=False)


class AgentRecord(Base, AgentMixin):
    __tablename__ = "agent_records"

    id: Mapped[str] = mapped_column(VARCHAR(100), primary_key=True)


class DelegationRecord(Base, DelegationMixin):
    __tablename__ = "delegation_records"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)


class CommandLedger(Base, CommandLedgerMixin):
    __tablename__ = "command_ledger"
    __table_args__ = (UniqueConstraint("command_id", "operation", name="uq_command_ledger_command_operation"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("control_sessions.id"),
        nullable=True,
    )


def register_models(registry: RegistryProtocol = DEFAULT_MODEL_REGISTRY) -> None:
    """Register all reference models with the ModelRegistry."""
    registry.register("PolicySnapshot", PolicySnapshot)
    registry.register("ControlSession", ControlSession)
    registry.register("SessionSeqCounter", SessionSeqCounter)
    registry.register("ControlEvent", ControlEvent)
    registry.register("ActionProposal", ActionProposal)
    registry.register("ApprovalTicket", ApprovalTicket)
    registry.register("AgentRecord", AgentRecord)
    registry.register("DelegationRecord", DelegationRecord)
    registry.register("CommandLedger", CommandLedger)


def create_tables(engine: Any) -> None:
    """Create all tables using the reference Base metadata.

    For async engines, use:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    """
    Base.metadata.create_all(engine)
