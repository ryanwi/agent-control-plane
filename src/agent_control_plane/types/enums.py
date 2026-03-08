"""Control plane enumerations."""

from enum import StrEnum
from typing import Final


class ActionTier(StrEnum):
    """Classification tier for agent actions."""

    BLOCKED = "blocked"
    ALWAYS_APPROVE = "always_approve"
    AUTO_APPROVE = "auto_approve"
    UNRESTRICTED = "unrestricted"


class RoutingResolutionStep(StrEnum):
    """Deterministic routing resolution steps for auditability."""

    EXPLICIT_ASSIGNMENT = "explicit_assignment"
    POLICY_LIST_MATCH = "policy_list_match"
    RISK_TIER_MATCH = "risk_tier_match"
    CAPABILITY_MATCH = "capability_match"
    DEFAULT_AGENT = "default_agent"


class ActionName(StrEnum):
    """Canonical action identifiers."""

    UNKNOWN = "unknown"
    BAN = "ban"
    UNBAN = "unban"
    STATUS = "status"
    REFUND = "refund"
    CHANGE_ADDRESS = "change_address"
    CHECK_BALANCE = "check_balance"
    CLOSE_ACCOUNT = "close_account"
    EXECUTE_TRADE = "execute_trade"
    WIRE_TRANSFER = "wire_transfer"
    LOG_INCIDENT = "log_incident"
    LOG_VIOLATION = "log_violation"
    RESET_PASSWORD = "reset_password"
    RESET_CREDENTIALS = "reset_credentials"
    BLOCK_IP = "block_ip"
    BAN_USER = "ban_user"
    HIDE_POST = "hide_post"
    ISOLATE_HOST = "isolate_host"
    CHECK_ORDER_STATUS = "check_order_status"
    FETCH_METRICS = "fetch_metrics"
    GET_LOGS = "get_logs"
    SCAN_VULNERABILITY = "scan_vulnerability"
    FETCH_LOGS = "fetch_logs"
    FLAG_CONTENT = "flag_content"
    DELETE_CLUSTER = "delete_cluster"
    WIPE_DISK = "wipe_disk"
    RESTART_POD = "restart_pod"
    SCALE_UP = "scale_up"
    DESCRIBE_RESOURCES = "describe_resources"
    LIST_INSTANCES = "list_instances"
    STOP_INSTANCE = "stop_instance"
    START_INSTANCE = "start_instance"
    REBOOT_INSTANCE = "reboot_instance"
    TERMINATE_INSTANCE = "terminate_instance"
    WIPE_DATABASE = "wipe_database"
    DELETE_VBC = "delete_vbc"


ActionValue = ActionName | str
_REGISTERED_ACTION_NAMES: set[str] = set()
_BUILTIN_ACTION_NAMES: Final[set[str]] = set(ActionName._value2member_map_.keys())


def register_action_names(names: list[str]) -> None:
    """Register additional domain-specific action names."""
    for name in names:
        normalized = name.strip().lower()
        if normalized and normalized not in _BUILTIN_ACTION_NAMES:
            _REGISTERED_ACTION_NAMES.add(normalized)


def clear_registered_action_names() -> None:
    """Clear dynamically registered action names (primarily for tests)."""
    _REGISTERED_ACTION_NAMES.clear()


def is_registered_action_name(value: str) -> bool:
    """Return True when the value is a built-in or dynamically registered action."""
    normalized = value.strip().lower()
    return normalized in _BUILTIN_ACTION_NAMES or normalized in _REGISTERED_ACTION_NAMES


def parse_action_name(value: ActionValue) -> ActionValue:
    """Parse user input into a known action; unknown values fail-closed."""
    if isinstance(value, ActionName):
        return value
    normalized = value.strip().lower()
    if normalized in ActionName._value2member_map_:
        return ActionName(normalized)
    if normalized in _REGISTERED_ACTION_NAMES:
        return normalized
    return ActionName.UNKNOWN


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

    @property
    def rank(self) -> int:
        """Numeric rank for comparison (higher is more risky)."""
        ranks = {
            RiskLevel.LOW: 1,
            RiskLevel.MEDIUM: 2,
            RiskLevel.HIGH: 3,
        }
        return ranks[self]

    def __le__(self, other: object) -> bool:
        if not isinstance(other, RiskLevel):
            return NotImplemented
        return self.rank <= other.rank

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, RiskLevel):
            return NotImplemented
        return self.rank >= other.rank

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, RiskLevel):
            return NotImplemented
        return self.rank < other.rank

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, RiskLevel):
            return NotImplemented
        return self.rank > other.rank


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


class AssetMatch(StrEnum):
    """Asset-classifier match outcomes."""

    MATCHED = "matched"
    UNMATCHED = "unmatched"


class AssetScope(StrEnum):
    """Policy/session asset scope semantics."""

    MATCHED_ONLY = "matched_only"


class AgentScope(StrEnum):
    """Logical scopes used in payloads for agent/system timeout controls."""

    AGENT_ABORT = "agent_abort"
    SYSTEM_HALT = "system_halt"
    AGENT_TIMEOUT = "agent_timeout"


class UnknownAppEventPolicy(StrEnum):
    """How to handle app-level events the mapper cannot resolve."""

    RAISE = "raise"
    IGNORE = "ignore"


class McpEventName(StrEnum):
    """Canonical MCP gateway app-level events."""

    TOOL_CALL_RECEIVED = "tool_call_received"
    TOOL_CALL_ALLOWED = "tool_call_allowed"
    TOOL_CALL_BLOCKED = "tool_call_blocked"
    TOOL_CALL_APPROVAL_REQUIRED = "tool_call_approval_required"
    TOOL_CALL_EXECUTED = "tool_call_executed"
    TOOL_CALL_FAILED = "tool_call_failed"
