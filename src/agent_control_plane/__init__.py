"""agent-control-plane: Embeddable governance framework for agentic AI."""

from agent_control_plane.engine.approval_gate import ApprovalGate
from agent_control_plane.engine.budget_tracker import BudgetExhaustedError, BudgetTracker
from agent_control_plane.engine.concurrency import (
    ConcurrencyGuard,
    CycleAlreadyActiveError,
    InstrumentLockedError,
    ResourceLockedError,
)
from agent_control_plane.engine.event_store import EventStore
from agent_control_plane.engine.kill_switch import KillSwitch
from agent_control_plane.engine.policy_engine import (
    AssetClassifier,
    DefaultAssetClassifier,
    PolicyEngine,
)
from agent_control_plane.engine.router import ProposalRouter, RoutingDecision
from agent_control_plane.engine.session_manager import SessionManager
from agent_control_plane.models.registry import ModelRegistry
from agent_control_plane.recovery.crash_recovery import CrashRecovery
from agent_control_plane.recovery.timeout_escalation import TimeoutEscalation
from agent_control_plane.types.approvals import (
    ApprovalDecisionRequest,
    ApprovalScopeDTO,
    ApprovalTicketDTO,
)
from agent_control_plane.types.enums import (
    AbortReason,
    ActionTier,
    ApprovalDecisionType,
    ApprovalStatus,
    EventKind,
    ExecutionIntentStatus,
    ExecutionMode,
    KillSwitchScope,
    ProposalStatus,
    RiskLevel,
    SessionStatus,
)
from agent_control_plane.types.frames import EventFrame, RequestFrame, ResponseFrame
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
from agent_control_plane.types.sessions import SessionCreate, SessionState, SessionSummary

__all__ = [
    # Enums
    "AbortReason",
    "ActionTier",
    "ApprovalDecisionType",
    "ApprovalStatus",
    "EventKind",
    "ExecutionIntentStatus",
    "KillSwitchScope",
    "ProposalStatus",
    "RiskLevel",
    "SessionStatus",
    "ExecutionMode",
    # Policy DTOs
    "ActionTiers",
    "AutoApproveConditions",
    "PolicySnapshotDTO",
    "RiskLimits",
    # Proposal DTOs
    "ActionProposalDTO",
    "ExecutionIntentDTO",
    "ExecutionResultDTO",
    "RiskDecisionDTO",
    # Session DTOs
    "SessionCreate",
    "SessionState",
    "SessionSummary",
    # Approval DTOs
    "ApprovalDecisionRequest",
    "ApprovalScopeDTO",
    "ApprovalTicketDTO",
    # Frames
    "EventFrame",
    "RequestFrame",
    "ResponseFrame",
    # Engine
    "ApprovalGate",
    "AssetClassifier",
    "BudgetExhaustedError",
    "BudgetTracker",
    "ConcurrencyGuard",
    "CycleAlreadyActiveError",
    "ResourceLockedError",
    "DefaultAssetClassifier",
    "EventStore",
    "InstrumentLockedError",
    "KillSwitch",
    "PolicyEngine",
    "ProposalRouter",
    "RoutingDecision",
    "SessionManager",
    # Recovery
    "CrashRecovery",
    "TimeoutEscalation",
    # Models
    "ModelRegistry",
]
