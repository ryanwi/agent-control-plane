"""agent-control-plane: Embeddable governance framework for agentic AI."""

from agent_control_plane.async_facade import AsyncControlPlaneFacade
from agent_control_plane.builders import (
    KillSwitchServices,
    SessionEventBudgetServices,
    build_kill_switch_stack,
    build_session_event_budget,
)
from agent_control_plane.engine.agent_registry import AgentRegistry, DelegationGuard
from agent_control_plane.engine.approval_gate import ApprovalGate
from agent_control_plane.engine.budget_tracker import BudgetExhaustedError, BudgetTracker
from agent_control_plane.engine.concurrency import (
    ConcurrencyGuard,
    CycleAlreadyActiveError,
    ResourceLockedError,
)
from agent_control_plane.engine.event_store import EventStore
from agent_control_plane.engine.kill_switch import KillSwitch
from agent_control_plane.engine.policy_engine import (
    AssetClassifier,
    DefaultAssetClassifier,
    DefaultRiskClassifier,
    PolicyEngine,
    RiskClassifier,
)
from agent_control_plane.engine.router import ProposalRouter, RoutingDecision
from agent_control_plane.engine.session_manager import SessionManager
from agent_control_plane.mcp import (
    ApprovalRequiredError,
    BudgetDeniedError,
    KillSwitchActiveError,
    McpEventMapper,
    McpGateway,
    McpGatewayConfig,
    McpGovernanceError,
    PolicyDeniedError,
    ToolCallContext,
    ToolCallResult,
    ToolExecutionError,
    ToolExecutor,
    ToolPolicyMap,
)
from agent_control_plane.models import (
    ActionProposal,
    AgentRecord,
    ApprovalTicket,
    ControlEvent,
    ControlSession,
    DelegationRecord,
    PolicySnapshot,
    SessionSeqCounter,
    create_tables,
    register_models,
)
from agent_control_plane.models import (
    Base as ReferenceBase,
)
from agent_control_plane.models.registry import ModelRegistry, ScopedModelRegistry
from agent_control_plane.recovery.crash_recovery import CrashRecovery
from agent_control_plane.recovery.timeout_escalation import TimeoutEscalation
from agent_control_plane.storage import (
    ApprovalRepository,
    AsyncApprovalRepository,
    AsyncEventRepository,
    AsyncProposalRepository,
    AsyncSessionRepository,
    AsyncSqlAlchemyApprovalRepo,
    AsyncSqlAlchemyEventRepo,
    AsyncSqlAlchemyProposalRepo,
    AsyncSqlAlchemySessionRepo,
    AsyncSqlAlchemyUnitOfWork,
    AsyncUnitOfWork,
    EventRepository,
    ProposalRepository,
    SessionRepository,
    SyncSqlAlchemyApprovalRepo,
    SyncSqlAlchemyEventRepo,
    SyncSqlAlchemyProposalRepo,
    SyncSqlAlchemySessionRepo,
    SyncSqlAlchemyUnitOfWork,
    SyncUnitOfWork,
)
from agent_control_plane.sync import (
    AppEventMapper,
    ControlPlaneFacade,
    DictEventMapper,
    KillResultDTO,
    MappedEventDTO,
    SessionLifecycleResult,
    SyncControlPlane,
    UnknownAppEventError,
)
from agent_control_plane.types.agents import (
    AgentCapability,
    AgentMetadata,
    DelegationProposal,
)
from agent_control_plane.types.approvals import (
    ApprovalDecisionRequest,
    ApprovalScopeDTO,
    ApprovalTicketDTO,
)
from agent_control_plane.types.enums import (
    AbortReason,
    ActionName,
    ActionTier,
    AgentScope,
    ApprovalDecisionType,
    ApprovalStatus,
    AssetMatch,
    AssetScope,
    EventKind,
    ExecutionIntentStatus,
    ExecutionMode,
    KillSwitchScope,
    McpEventName,
    ProposalStatus,
    RiskLevel,
    RoutingResolutionStep,
    SessionStatus,
    UnknownAppEventPolicy,
)
from agent_control_plane.types.frames import EventFrame, RequestFrame, ResponseFrame
from agent_control_plane.types.ids import AgentId, IdempotencyKey, ResourceId
from agent_control_plane.types.policies import (
    ActionTiers,
    AutoApproveConditions,
    PolicySnapshotDTO,
    RiskLimits,
)
from agent_control_plane.types.proposals import (
    ActionProposalDTO,
    ExecutionIntentDTO,
    ExecutionResultDTO,
    RiskDecisionDTO,
)
from agent_control_plane.types.sessions import BudgetInfo, KillSwitchResult, SessionCreate, SessionState, SessionSummary

__all__ = [
    # Enums
    "AbortReason",
    "ActionName",
    "ActionProposal",
    # Proposal DTOs
    "ActionProposalDTO",
    "ActionTier",
    # Policy DTOs
    "ActionTiers",
    # Agent Registry
    "AgentCapability",
    "AgentId",
    "AgentMetadata",
    "AgentRecord",
    "AgentRegistry",
    "AgentScope",
    "AppEventMapper",
    # Approval DTOs
    "ApprovalDecisionRequest",
    "ApprovalDecisionType",
    # Engine
    "ApprovalGate",
    # Storage protocols/backends
    "ApprovalRepository",
    "ApprovalRequiredError",
    "ApprovalScopeDTO",
    "ApprovalStatus",
    "ApprovalTicket",
    "ApprovalTicketDTO",
    "AssetClassifier",
    "AssetMatch",
    "AssetScope",
    "AsyncApprovalRepository",
    "AsyncControlPlaneFacade",
    "AsyncEventRepository",
    "AsyncProposalRepository",
    "AsyncSessionRepository",
    "AsyncSqlAlchemyApprovalRepo",
    "AsyncSqlAlchemyEventRepo",
    "AsyncSqlAlchemyProposalRepo",
    "AsyncSqlAlchemySessionRepo",
    "AsyncSqlAlchemyUnitOfWork",
    "AsyncUnitOfWork",
    "AutoApproveConditions",
    "BudgetDeniedError",
    "BudgetExhaustedError",
    "BudgetInfo",
    "BudgetTracker",
    "ConcurrencyGuard",
    "ControlEvent",
    "ControlPlaneFacade",
    "ControlSession",
    # Recovery
    "CrashRecovery",
    "CycleAlreadyActiveError",
    "DefaultAssetClassifier",
    "DefaultRiskClassifier",
    "DelegationGuard",
    "DelegationProposal",
    "DelegationRecord",
    "DictEventMapper",
    # Frames
    "EventFrame",
    "EventKind",
    "EventRepository",
    "EventStore",
    "ExecutionIntentDTO",
    "ExecutionIntentStatus",
    "ExecutionMode",
    "ExecutionResultDTO",
    "IdempotencyKey",
    "KillResultDTO",
    "KillSwitch",
    "KillSwitchActiveError",
    "KillSwitchResult",
    "KillSwitchScope",
    "KillSwitchServices",
    "MappedEventDTO",
    "McpEventMapper",
    "McpEventName",
    "McpGateway",
    "McpGatewayConfig",
    "McpGovernanceError",
    # Models
    "ModelRegistry",
    "PolicyDeniedError",
    "PolicyEngine",
    "PolicySnapshot",
    "PolicySnapshotDTO",
    "ProposalRepository",
    "ProposalRouter",
    "ProposalStatus",
    "ReferenceBase",
    "RequestFrame",
    "ResourceId",
    "ResourceLockedError",
    "ResponseFrame",
    "RiskClassifier",
    "RiskDecisionDTO",
    "RiskLevel",
    "RiskLimits",
    "RoutingDecision",
    "RoutingResolutionStep",
    "ScopedModelRegistry",
    # Session DTOs
    "SessionCreate",
    "SessionEventBudgetServices",
    "SessionLifecycleResult",
    "SessionManager",
    "SessionRepository",
    "SessionSeqCounter",
    "SessionState",
    "SessionStatus",
    "SessionSummary",
    "SyncControlPlane",
    "SyncSqlAlchemyApprovalRepo",
    "SyncSqlAlchemyEventRepo",
    "SyncSqlAlchemyProposalRepo",
    "SyncSqlAlchemySessionRepo",
    "SyncSqlAlchemyUnitOfWork",
    "SyncUnitOfWork",
    "TimeoutEscalation",
    "ToolCallContext",
    "ToolCallResult",
    "ToolExecutionError",
    "ToolExecutor",
    "ToolPolicyMap",
    "UnknownAppEventError",
    "UnknownAppEventPolicy",
    "build_kill_switch_stack",
    "build_session_event_budget",
    "create_tables",
    "register_models",
]
