"""agent-control-plane: Embeddable governance framework for agentic AI."""

# ruff: noqa: RUF022

from importlib.metadata import PackageNotFoundError, version

from agent_control_plane.async_facade import AsyncControlPlaneFacade
from agent_control_plane.benchmark import (
    FitnessEvaluator,
    ScenarioRunner,
    WeightedFitnessEvaluator,
    hash_config,
    run_batch,
    run_benchmark,
)
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
from agent_control_plane.engine.model_governor import ModelAccessDeniedError, ModelGovernor
from agent_control_plane.engine.policy_engine import (
    AssetClassifier,
    DefaultAssetClassifier,
    DefaultRiskClassifier,
    PolicyEngine,
    RiskClassifier,
)
from agent_control_plane.engine.router import ProposalRouter, RoutingDecision
from agent_control_plane.engine.session_manager import SessionManager
from agent_control_plane.engine.session_risk_accumulator import SessionRiskAccumulator
from agent_control_plane.engine.token_budget_tracker import TokenBudgetExhaustedError, TokenBudgetTracker
from agent_control_plane.idempotency import proposal_command_id
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
    ActionProposalRow,
    AgentRecord,
    ApprovalTicketRow,
    CommandLedger,
    ControlEvent,
    ControlSession,
    DelegationRecord,
    PolicySnapshotRow,
    SessionSeqCounter,
    TokenBudgetConfigRow,
    TokenBudgetStateRow,
    TokenUsageLedgerRow,
    create_tables,
    register_models,
)
from agent_control_plane.models import (
    Base as ReferenceBase,
)
from agent_control_plane.models.registry import ModelRegistry, ScopedModelRegistry
from agent_control_plane.policies import (
    EvaluatorPolicy,
    GuardrailPolicy,
    PassThroughGuardrailPolicy,
    ThresholdEvaluatorPolicy,
)
from agent_control_plane.recovery.crash_recovery import CrashRecovery
from agent_control_plane.recovery.timeout_escalation import TimeoutEscalation
from agent_control_plane.storage import (
    ApprovalRepository,
    AsyncApprovalRepository,
    AsyncCommandRepository,
    AsyncEventRepository,
    AsyncProposalRepository,
    AsyncSessionRepository,
    AsyncSqlAlchemyApprovalRepo,
    AsyncSqlAlchemyCommandRepo,
    AsyncSqlAlchemyEventRepo,
    AsyncSqlAlchemyProposalRepo,
    AsyncSqlAlchemySessionRepo,
    AsyncSqlAlchemyTokenBudgetRepo,
    AsyncSqlAlchemyUnitOfWork,
    AsyncUnitOfWork,
    CommandRepository,
    EventRepository,
    ProposalRepository,
    SessionRepository,
    SyncSqlAlchemyApprovalRepo,
    SyncSqlAlchemyCommandRepo,
    SyncSqlAlchemyEventRepo,
    SyncSqlAlchemyProposalRepo,
    SyncSqlAlchemySessionRepo,
    SyncSqlAlchemyTokenBudgetRepo,
    SyncSqlAlchemyUnitOfWork,
    SyncUnitOfWork,
)
from agent_control_plane.storage.protocols import AsyncTokenBudgetRepository, TokenBudgetRepository
from agent_control_plane.sync import (
    AppEventMapper,
    ControlPlaneFacade,
    DictEventMapper,
    KillResult,
    MappedEvent,
    SessionLifecycleResult,
    SyncControlPlane,
    UnknownAppEventError,
)
from agent_control_plane.telemetry import MeterLike, TracerLike, export_event, export_scorecard
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
from agent_control_plane.types.agents import (
    AgentCapability,
    AgentMetadata,
    DelegationProposal,
)
from agent_control_plane.types.aliases import (
    AliasProfile,
    AliasRegistry,
    FieldAliasMap,
    apply_inbound_aliases,
    apply_outbound_aliases,
)
from agent_control_plane.types.approvals import (
    ApprovalDecisionRequest,
    ApprovalScope,
    ApprovalTicket,
)
from agent_control_plane.types.benchmark import (
    BenchmarkRunResult,
    BenchmarkRunSpec,
    BenchmarkScenarioSpec,
    FitnessWeights,
)
from agent_control_plane.types.enums import (
    AbortReason,
    ActionName,
    ActionTier,
    ActionValue,
    AgentScope,
    ApprovalDecisionType,
    ApprovalStatus,
    AssetMatch,
    AssetScope,
    BudgetPeriod,
    EvaluationDecision,
    EventKind,
    ExecutionIntentStatus,
    ExecutionMode,
    GoalStatus,
    GuardrailPhase,
    KillSwitchScope,
    McpEventName,
    ModelTier,
    PlanStepStatus,
    ProposalStatus,
    RiskLevel,
    RoutingResolutionStep,
    SessionStatus,
    UnknownAppEventPolicy,
    clear_registered_action_names,
    is_registered_action_name,
    register_action_names,
)
from agent_control_plane.types.extensions import (
    clear_metadata_schemas,
    clear_risk_limits_extension_schema,
    register_metadata_schema,
    register_risk_limits_extension_schema,
)
from agent_control_plane.types.frames import EventFrame, RequestFrame, ResponseFrame
from agent_control_plane.types.ids import AgentId, IdempotencyKey, ModelId, OrgId, ResourceId, TeamId, UserId
from agent_control_plane.types.policies import (
    ActionTiers,
    AutoApproveConditions,
    PolicySnapshot,
    RiskLimits,
)
from agent_control_plane.types.proposals import (
    ActionProposal,
    ExecutionIntent,
    ExecutionResult,
    RiskDecision,
)
from agent_control_plane.types.query import (
    CommandResult,
    Page,
    SessionHealth,
    StateChange,
    StateChangePage,
)
from agent_control_plane.types.risk import RiskPattern, SessionRiskEscalation, SessionRiskState
from agent_control_plane.types.sessions import BudgetInfo, KillSwitchResult, SessionCreate, SessionState, SessionSummary
from agent_control_plane.types.token_governance import (
    IdentityContext,
    ModelAccessResult,
    ModelGovernancePolicy,
    TokenBudgetCheckResult,
    TokenBudgetConfig,
    TokenBudgetState,
    TokenUsage,
    TokenUsageSummary,
)

try:
    __version__ = version("agent-control-plane")
except PackageNotFoundError:
    __version__ = "0.0.0+local"


def get_version() -> str:
    """Return installed package version."""
    return __version__


__all__ = [
    "__version__",
    # Enums
    "AbortReason",
    "ActionName",
    "ActionValue",
    "ActionProposal",
    "ActionTier",
    # Policy
    "ActionTiers",
    # Agent Registry
    "AgentCapability",
    "AgentId",
    "AgentMetadata",
    "AgentRecord",
    "AgentRegistry",
    "AgentScope",
    "AppEventMapper",
    # Approval
    "ApprovalDecisionRequest",
    "ApprovalDecisionType",
    # Engine
    "ApprovalGate",
    # Storage protocols/backends
    "ApprovalRepository",
    "ApprovalRequiredError",
    "ApprovalScope",
    "ApprovalStatus",
    "ApprovalTicket",
    "AssetClassifier",
    "AssetMatch",
    "AssetScope",
    "AliasProfile",
    "AliasRegistry",
    "AsyncApprovalRepository",
    "AsyncCommandRepository",
    "AsyncControlPlaneFacade",
    "AsyncEventRepository",
    "AsyncProposalRepository",
    "AsyncSessionRepository",
    "AsyncSqlAlchemyApprovalRepo",
    "AsyncSqlAlchemyCommandRepo",
    "AsyncSqlAlchemyEventRepo",
    "AsyncSqlAlchemyProposalRepo",
    "AsyncSqlAlchemySessionRepo",
    "AsyncSqlAlchemyTokenBudgetRepo",
    "AsyncSqlAlchemyUnitOfWork",
    "AsyncUnitOfWork",
    "AutoApproveConditions",
    "BenchmarkRunResult",
    "BenchmarkRunSpec",
    "BenchmarkScenarioSpec",
    "BudgetDeniedError",
    "BudgetExhaustedError",
    "BudgetInfo",
    "BudgetPeriod",
    "BudgetTracker",
    "CommandLedger",
    "CommandRepository",
    "CommandResult",
    "ControlPlaneScorecard",
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
    "EvaluationDecision",
    "EvaluationResult",
    "EventRepository",
    "EventStore",
    "ExecutionIntent",
    "ExecutionIntentStatus",
    "ExecutionMode",
    "ExecutionResult",
    "export_event",
    "export_scorecard",
    "FieldAliasMap",
    "FitnessEvaluator",
    "FitnessWeights",
    "Goal",
    "GoalStatus",
    "GuardrailDecision",
    "GuardrailPhase",
    "HandoffResult",
    "hash_config",
    "apply_inbound_aliases",
    "apply_outbound_aliases",
    "IdentityContext",
    "IdempotencyKey",
    "KillResult",
    "KillSwitch",
    "KillSwitchActiveError",
    "KillSwitchResult",
    "KillSwitchScope",
    "KillSwitchServices",
    "MappedEvent",
    "McpEventMapper",
    "McpEventName",
    "McpGateway",
    "McpGatewayConfig",
    "McpGovernanceError",
    "clear_metadata_schemas",
    "clear_registered_action_names",
    "clear_risk_limits_extension_schema",
    "is_registered_action_name",
    "ModelAccessDeniedError",
    "ModelAccessResult",
    "ModelGovernancePolicy",
    "ModelGovernor",
    "ModelId",
    # Models
    "ModelRegistry",
    "ModelTier",
    "PolicyDeniedError",
    "PolicyEngine",
    "PolicySnapshotRow",
    "PolicySnapshot",
    "ActionProposalRow",
    "ApprovalTicketRow",
    "Plan",
    "PlanProgress",
    "PlanStep",
    "PlanStepStatus",
    "Page",
    "ProposalRepository",
    "ProposalRouter",
    "ProposalStatus",
    "proposal_command_id",
    "ReferenceBase",
    "RequestFrame",
    "ResourceId",
    "ResourceLockedError",
    "ResponseFrame",
    "RollbackResult",
    "RiskClassifier",
    "RiskDecision",
    "RiskLevel",
    "RiskPattern",
    "RiskLimits",
    "RoutingDecision",
    "RoutingResolutionStep",
    "ScopedModelRegistry",
    "SessionRiskAccumulator",
    "SessionRiskEscalation",
    "SessionRiskState",
    # Session
    "SessionCreate",
    "SessionCheckpoint",
    "SessionEventBudgetServices",
    "SessionLifecycleResult",
    "SessionManager",
    "SessionHealth",
    "SessionRepository",
    "SessionSeqCounter",
    "SessionState",
    "SessionStatus",
    "SessionSummary",
    "SyncControlPlane",
    "SyncSqlAlchemyApprovalRepo",
    "SyncSqlAlchemyCommandRepo",
    "SyncSqlAlchemyEventRepo",
    "SyncSqlAlchemyProposalRepo",
    "SyncSqlAlchemySessionRepo",
    "SyncSqlAlchemyTokenBudgetRepo",
    "SyncSqlAlchemyUnitOfWork",
    "SyncUnitOfWork",
    "StateChange",
    "StateChangePage",
    "TeamId",
    "TimeoutEscalation",
    "TokenBudgetCheckResult",
    "TokenBudgetConfig",
    "TokenBudgetConfigRow",
    "TokenBudgetExhaustedError",
    "TokenBudgetRepository",
    "AsyncTokenBudgetRepository",
    "TokenBudgetState",
    "TokenBudgetStateRow",
    "TokenBudgetTracker",
    "TokenUsage",
    "TokenUsageLedgerRow",
    "TokenUsageSummary",
    "ThresholdEvaluatorPolicy",
    "TracerLike",
    "ToolCallContext",
    "ToolCallResult",
    "ToolExecutionError",
    "ToolExecutor",
    "ToolPolicyMap",
    "UserId",
    "OrgId",
    "UnknownAppEventError",
    "UnknownAppEventPolicy",
    "build_kill_switch_stack",
    "build_session_event_budget",
    "EvaluatorPolicy",
    "GuardrailPolicy",
    "PassThroughGuardrailPolicy",
    "run_batch",
    "run_benchmark",
    "ScenarioRunner",
    "create_tables",
    "get_version",
    "register_action_names",
    "register_metadata_schema",
    "register_risk_limits_extension_schema",
    "register_models",
    "MeterLike",
    "WeightedFitnessEvaluator",
]
