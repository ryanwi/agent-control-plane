"""Long-running autonomous agent demo with ACP governance windows.

This demonstrates how a continuously running worker can rotate ACP sessions
on a fixed cadence while remaining unattended for long periods.

Run:
    uv run python examples/long_running_autonomous_agent.py --horizon day
    uv run python examples/long_running_autonomous_agent.py --horizon month --max-cycles 240
"""

from __future__ import annotations

from argparse import ArgumentParser
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from time import sleep
from uuid import UUID

from agent_control_plane.sync import ControlPlaneFacade, DictEventMapper
from agent_control_plane.types.enums import EventKind
from agent_control_plane.types.proposals import ActionProposal

try:
    from examples.governance_demo_common import GovernanceDecision, apply_governance_decision
except ModuleNotFoundError:  # pragma: no cover - supports direct script execution
    from governance_demo_common import GovernanceDecision, apply_governance_decision


class Horizon(StrEnum):
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class CasePriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True)
class HorizonProfile:
    max_cycles: int
    rotate_session_every_cycles: int
    checkpoint_every_cycles: int
    max_cost_per_session: Decimal
    max_actions_per_session: int
    recommended_real_cycle_interval: str


PROFILES: dict[Horizon, HorizonProfile] = {
    Horizon.HOUR: HorizonProfile(
        max_cycles=12,
        rotate_session_every_cycles=12,
        checkpoint_every_cycles=6,
        max_cost_per_session=Decimal("15.00"),
        max_actions_per_session=30,
        recommended_real_cycle_interval="5 minutes",
    ),
    Horizon.DAY: HorizonProfile(
        max_cycles=48,
        rotate_session_every_cycles=24,
        checkpoint_every_cycles=12,
        max_cost_per_session=Decimal("40.00"),
        max_actions_per_session=96,
        recommended_real_cycle_interval="15-30 minutes",
    ),
    Horizon.WEEK: HorizonProfile(
        max_cycles=168,
        rotate_session_every_cycles=24,
        checkpoint_every_cycles=24,
        max_cost_per_session=Decimal("150.00"),
        max_actions_per_session=480,
        recommended_real_cycle_interval="30-60 minutes",
    ),
    Horizon.MONTH: HorizonProfile(
        max_cycles=720,
        rotate_session_every_cycles=24,
        checkpoint_every_cycles=24,
        max_cost_per_session=Decimal("500.00"),
        max_actions_per_session=2000,
        recommended_real_cycle_interval="60 minutes",
    ),
}


@dataclass(frozen=True)
class CaseWorkItem:
    case_id: str
    priority: CasePriority


@dataclass
class RunStats:
    approved: int = 0
    denied: int = 0
    session_sequence: int = 0
    cycles_in_session: int = 0


def _case_for_cycle(cycle_no: int) -> CaseWorkItem:
    # Deterministic workload mix for unattended-operation simulation.
    if cycle_no % 17 == 0:
        return CaseWorkItem(case_id=f"case-{9000 + cycle_no}", priority=CasePriority.CRITICAL)
    if cycle_no % 5 == 0:
        return CaseWorkItem(case_id=f"case-{9000 + cycle_no}", priority=CasePriority.HIGH)
    if cycle_no % 2 == 0:
        return CaseWorkItem(case_id=f"case-{9000 + cycle_no}", priority=CasePriority.MEDIUM)
    return CaseWorkItem(case_id=f"case-{9000 + cycle_no}", priority=CasePriority.LOW)


def _decision_for_case(case: CaseWorkItem) -> GovernanceDecision:
    # Unattended default policy: deny high-risk work, approve routine work.
    if case.priority in (CasePriority.HIGH, CasePriority.CRITICAL):
        return GovernanceDecision.DENY
    return GovernanceDecision.APPROVE


class LongRunningSupportAgent:
    def __init__(self, db_path: Path) -> None:
        mapper = DictEventMapper(
            {
                "agent_loop_started": EventKind.CYCLE_STARTED,
                "agent_loop_finished": EventKind.CYCLE_COMPLETED,
            }
        )
        self.cp = ControlPlaneFacade.from_database_url(f"sqlite:///{db_path}", mapper=mapper)
        self.cp.setup()

    def _open_session(self, *, horizon: Horizon, sequence: int, profile: HorizonProfile) -> UUID:
        return self.cp.open_session(
            f"continuous-{horizon.value}-session-{sequence}",
            max_cost=profile.max_cost_per_session,
            max_action_count=profile.max_actions_per_session,
            command_id=f"{horizon.value}-session-{sequence}-open",
        )

    def _close_session(self, *, horizon: Horizon, sequence: int, session_id: UUID, cycles_completed: int) -> None:
        self.cp.close_session(
            session_id,
            payload={"summary": "session window complete", "cycles_completed": cycles_completed},
            command_id=f"{horizon.value}-session-{sequence}-close",
        )

    def _rotate_session_if_needed(
        self,
        *,
        horizon: Horizon,
        profile: HorizonProfile,
        stats: RunStats,
        session_id: UUID | None,
        session_ids: list[UUID],
    ) -> UUID:
        should_rotate = session_id is None or stats.cycles_in_session >= profile.rotate_session_every_cycles
        if not should_rotate:
            assert session_id is not None
            return session_id

        if session_id is not None:
            self._close_session(
                horizon=horizon,
                sequence=stats.session_sequence,
                session_id=session_id,
                cycles_completed=stats.cycles_in_session,
            )

        stats.session_sequence += 1
        next_session_id = self._open_session(horizon=horizon, sequence=stats.session_sequence, profile=profile)
        session_ids.append(next_session_id)
        stats.cycles_in_session = 0
        print(f"session_opened={next_session_id} session_sequence={stats.session_sequence}")
        return next_session_id

    def _run_cycle(
        self,
        *,
        horizon: Horizon,
        profile: HorizonProfile,
        session_id: UUID,
        cycle_no: int,
        stats: RunStats,
    ) -> None:
        case = _case_for_cycle(cycle_no)
        decision = _decision_for_case(case)

        proposal = ActionProposal(
            session_id=session_id,
            resource_id=case.case_id,
            resource_type="customer_case",
            decision="status",
            reasoning=f"autonomous support cycle {cycle_no}",
            metadata={
                "cycle": cycle_no,
                "priority": case.priority.value,
                "horizon": horizon.value,
                "session_sequence": stats.session_sequence,
            },
            weight=Decimal("0.40"),
            score=Decimal("0.70"),
        )
        proposal = self.cp.create_proposal(
            proposal,
            command_id=f"{horizon.value}-session-{stats.session_sequence}-cycle-{cycle_no}-proposal",
        )

        ticket = self.cp.create_ticket(
            session_id,
            proposal.id,
            timeout_at=datetime.now(UTC) + timedelta(minutes=15),
            command_id=f"{horizon.value}-session-{stats.session_sequence}-cycle-{cycle_no}-ticket",
        )

        if decision is GovernanceDecision.APPROVE:
            has_budget = self.cp.check_budget(session_id, cost=proposal.weight, action_count=1)
            if not has_budget:
                decision = GovernanceDecision.DENY
                self.cp.emit(
                    session_id,
                    EventKind.BUDGET_EXHAUSTED,
                    {"cycle": cycle_no, "case_id": case.case_id},
                    state_bearing=True,
                    agent_id="long-running-support-agent",
                    command_id=(f"{horizon.value}-session-{stats.session_sequence}-cycle-{cycle_no}-budget-exhausted"),
                )

        status = apply_governance_decision(
            cp=self.cp,
            session_id=session_id,
            proposal=proposal,
            ticket_id=ticket.id,
            decision=decision,
            provider="autonomous_policy",
            decided_by="policy-engine",
            agent_id="long-running-support-agent",
            reason=f"priority={case.priority.value}",
            command_prefix=f"{horizon.value}-session-{stats.session_sequence}-cycle-{cycle_no}",
        )

        if status == "APPROVED":
            stats.approved += 1
            self.cp.increment_budget(session_id, cost=proposal.weight, action_count=1)
        else:
            stats.denied += 1

        if cycle_no % profile.checkpoint_every_cycles == 0:
            checkpoint = self.cp.create_checkpoint(
                session_id,
                label=f"{horizon.value}-cycle-{cycle_no}",
                metadata={"cycle": cycle_no, "session_sequence": stats.session_sequence},
                created_by="long-running-support-agent",
                command_id=f"{horizon.value}-session-{stats.session_sequence}-cycle-{cycle_no}-checkpoint",
            )
            print(f"checkpoint_created={checkpoint.id} cycle={cycle_no}")

        if cycle_no % 10 == 0:
            print(
                f"cycle={cycle_no} case={case.case_id} priority={case.priority.value} "
                f"decision={decision.value} approved={stats.approved} denied={stats.denied}"
            )

    def _approval_event_counts(self, session_ids: list[UUID]) -> tuple[int, int]:
        granted_events = 0
        denied_events = 0
        for sid in session_ids:
            for event in self.cp.replay(sid, after_seq=0, limit=10_000):
                if event.event_kind == EventKind.APPROVAL_GRANTED:
                    granted_events += 1
                elif event.event_kind == EventKind.APPROVAL_DENIED:
                    denied_events += 1
        return granted_events, denied_events

    def run(
        self,
        *,
        horizon: Horizon,
        max_cycles: int,
        cycle_sleep_seconds: float,
    ) -> None:
        profile = PROFILES[horizon]
        stats = RunStats()
        session_id: UUID | None = None
        session_ids: list[UUID] = []

        print(f"horizon={horizon.value}")
        print(f"recommended_real_cycle_interval={profile.recommended_real_cycle_interval}")
        print(f"simulation_cycles={max_cycles}")
        print("worker_mode=continuous")

        for cycle_no in range(1, max_cycles + 1):
            session_id = self._rotate_session_if_needed(
                horizon=horizon,
                profile=profile,
                stats=stats,
                session_id=session_id,
                session_ids=session_ids,
            )
            stats.cycles_in_session += 1
            self._run_cycle(
                horizon=horizon,
                profile=profile,
                session_id=session_id,
                cycle_no=cycle_no,
                stats=stats,
            )
            sleep(cycle_sleep_seconds)

        if session_id is not None:
            self._close_session(
                horizon=horizon,
                sequence=stats.session_sequence,
                session_id=session_id,
                cycles_completed=stats.cycles_in_session,
            )
        if max_cycles % 10 != 0:
            last_case = _case_for_cycle(max_cycles)
            print(
                f"cycle={max_cycles} case={last_case.case_id} priority={last_case.priority.value} "
                f"approved={stats.approved} denied={stats.denied}"
            )

        granted_events, denied_events = self._approval_event_counts(session_ids)
        scorecard = self.cp.get_operational_scorecard()
        print(
            "scorecard="
            f"events={scorecard.total_events} "
            f"approvals={granted_events} "
            f"denials={denied_events} "
            f"budget_exhaustions={scorecard.budget_exhausted_count}"
        )
        print(f"run_complete sessions={stats.session_sequence} approved={stats.approved} denied={stats.denied}")


def main() -> None:
    parser = ArgumentParser(description="Run long-running unattended agent governance demo")
    parser.add_argument("--horizon", choices=[h.value for h in Horizon], default=Horizon.DAY.value)
    parser.add_argument("--db", default="./long_running_continuous_demo.db", help="SQLite database path")
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=0,
        help="Override default cycle count for the selected horizon (0 uses profile default)",
    )
    parser.add_argument(
        "--cycle-sleep-seconds",
        type=float,
        default=0.05,
        help="Sleep duration between cycles for simulation",
    )
    args = parser.parse_args()

    horizon = Horizon(args.horizon)
    profile = PROFILES[horizon]
    max_cycles = args.max_cycles if args.max_cycles > 0 else profile.max_cycles

    db_path = Path(args.db)
    db_path.unlink(missing_ok=True)

    agent = LongRunningSupportAgent(db_path)
    agent.run(
        horizon=horizon,
        max_cycles=max_cycles,
        cycle_sleep_seconds=args.cycle_sleep_seconds,
    )
    print(f"db_path={db_path}")


if __name__ == "__main__":
    main()
