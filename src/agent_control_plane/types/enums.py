"""Control plane enumerations."""

from enum import StrEnum


class ActionTier(StrEnum):
    """Classification tier for agent actions."""

    BLOCKED = "blocked"
    ALWAYS_APPROVE = "always_approve"
    AUTO_APPROVE = "auto_approve"
    UNRESTRICTED = "unrestricted"


class SessionStatus(StrEnum):
    """Control session lifecycle states."""

    CREATED = "created"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ABORTED = "aborted"


class EventKind(StrEnum):
    """Types of control plane events."""

    CYCLE_STARTED = "cycle_started"
    CYCLE_SKIPPED = "cycle_skipped"
    CYCLE_COMPLETED = "cycle_completed"
    CYCLE_RECOVERED = "cycle_recovered"
    SIGNALS_CALCULATED = "signals_calculated"
    PROPOSALS_GENERATED = "proposals_generated"
    RISK_ASSESSED = "risk_assessed"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_DENIED = "approval_denied"
    APPROVAL_TIMEOUT = "approval_timeout"
    EXECUTION_STARTED = "execution_started"
    EXECUTION_COMPLETED = "execution_completed"
    SESSION_ABORTED = "session_aborted"
    KILL_SWITCH_TRIGGERED = "kill_switch_triggered"
    BUDGET_EXHAUSTED = "budget_exhausted"


class ProposalStatus(StrEnum):
    """Action proposal lifecycle states."""

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    EXECUTED = "executed"
    FAILED = "failed"


class ApprovalStatus(StrEnum):
    """Approval ticket lifecycle states."""

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


class ApprovalDecisionType(StrEnum):
    """Scope of an approval decision."""

    ALLOW_ONCE = "allow_once"
    ALLOW_FOR_SESSION = "allow_for_session"


class RiskLevel(StrEnum):
    """Risk classification levels for proposals."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ExecutionMode(StrEnum):
    """Execution mode for agent sessions."""

    DRY_RUN = "dry_run"
    LIVE = "live"
    REPLAY = "replay"


class AbortReason(StrEnum):
    """Reasons for session/cycle abortion."""

    OPERATOR_REQUEST = "operator_request"
    KILL_SWITCH = "kill_switch"
    BUDGET_EXHAUSTED = "budget_exhausted"
    AGENT_TIMEOUT = "agent_timeout"
    SYSTEM_ERROR = "system_error"
    POLICY_VIOLATION = "policy_violation"


class KillSwitchScope(StrEnum):
    """Kill switch scope levels."""

    SESSION_ABORT = "session_abort"
    AGENT_ABORT = "agent_abort"
    SYSTEM_HALT = "system_halt"
    BUDGET_AUTO_HALT = "budget_auto_halt"


class ExecutionIntentStatus(StrEnum):
    """Execution intent lifecycle states."""

    PENDING = "pending"
    SUBMITTED = "submitted"
    FILLED = "filled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
