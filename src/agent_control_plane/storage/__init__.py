"""Storage abstraction layer for the control plane."""

from agent_control_plane.storage.protocols import (
    ApprovalRepository,
    AsyncApprovalRepository,
    AsyncEventRepository,
    AsyncProposalRepository,
    AsyncSessionRepository,
    AsyncUnitOfWork,
    EventRepository,
    ProposalRepository,
    SessionRepository,
    SyncUnitOfWork,
)
from agent_control_plane.storage.sqlalchemy_async import (
    AsyncSqlAlchemyApprovalRepo,
    AsyncSqlAlchemyEventRepo,
    AsyncSqlAlchemyProposalRepo,
    AsyncSqlAlchemySessionRepo,
    AsyncSqlAlchemyUnitOfWork,
)
from agent_control_plane.storage.sqlalchemy_sync import (
    SyncSqlAlchemyApprovalRepo,
    SyncSqlAlchemyEventRepo,
    SyncSqlAlchemyProposalRepo,
    SyncSqlAlchemySessionRepo,
    SyncSqlAlchemyUnitOfWork,
)

__all__ = [
    "ApprovalRepository",
    "AsyncApprovalRepository",
    "AsyncEventRepository",
    "AsyncProposalRepository",
    "AsyncSessionRepository",
    "AsyncSqlAlchemyApprovalRepo",
    "AsyncSqlAlchemyEventRepo",
    "AsyncSqlAlchemyProposalRepo",
    "AsyncSqlAlchemySessionRepo",
    "AsyncSqlAlchemyUnitOfWork",
    "AsyncUnitOfWork",
    "EventRepository",
    "ProposalRepository",
    "SessionRepository",
    "SyncSqlAlchemyApprovalRepo",
    "SyncSqlAlchemyEventRepo",
    "SyncSqlAlchemyProposalRepo",
    "SyncSqlAlchemySessionRepo",
    "SyncSqlAlchemyUnitOfWork",
    "SyncUnitOfWork",
]
