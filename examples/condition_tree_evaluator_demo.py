"""Condition tree and evaluator plugin demo.

Demonstrates:
1. Building composite condition trees (and/or/not) for policy rules.
2. Pluggable evaluators (regex, list) registered in EvaluatorRegistry.
3. EvaluatorCondition leaves that delegate to registry evaluators.
4. ConditionEvaluator engine evaluating trees against proposals.
5. Parallel evaluation of multiple evaluators with cancel-on-deny.

Run:
    uv run python examples/condition_tree_evaluator_demo.py
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import uuid4

from agent_control_plane.engine.condition_evaluator import ConditionEvaluator
from agent_control_plane.engine.parallel_evaluator import ParallelPolicyEvaluator
from agent_control_plane.evaluators import (
    EvaluatorRegistry,
    ListEvaluator,
    ListEvaluatorConfig,
    RegexEvaluator,
    RegexEvaluatorConfig,
)
from agent_control_plane.types.conditions import (
    ActionCondition,
    AndCondition,
    EvaluatorCondition,
    NotCondition,
    OrCondition,
    RiskLevelCondition,
    ScoreCondition,
    WeightCondition,
)
from agent_control_plane.types.enums import ActionName, ExecutionMode, RiskLevel
from agent_control_plane.types.policies import PolicySnapshot
from agent_control_plane.types.proposals import ActionProposal

POLICY = PolicySnapshot(
    action_tiers={
        "blocked": [],
        "always_approve": [],
        "auto_approve": [ActionName.STATUS],
        "unrestricted": [],
    },
    execution_mode=ExecutionMode.DRY_RUN,
)


def _proposal(**overrides) -> ActionProposal:
    defaults = {
        "session_id": uuid4(),
        "resource_id": "res-001",
        "resource_type": "task",
        "decision": ActionName.STATUS,
        "reasoning": "demo",
        "weight": Decimal("1.0"),
        "score": Decimal("0.85"),
    }
    defaults.update(overrides)
    return ActionProposal(**defaults)


# ── 1. Basic condition trees ────────────────────────────────────


async def demo_basic_trees() -> None:
    print("=== 1. Basic condition trees ===\n")
    ev = ConditionEvaluator()

    # Simple leaf conditions
    low_risk = RiskLevelCondition(level=RiskLevel.LOW, operator="le")
    good_score = ScoreCondition(min_score=Decimal("0.7"))
    small_weight = WeightCondition(max_weight=Decimal("2.0"))

    proposal = _proposal()
    for name, node in [("risk<=LOW", low_risk), ("score>=0.7", good_score), ("weight<=2.0", small_weight)]:
        result = await ev.evaluate(node, proposal, RiskLevel.LOW, POLICY)
        print(f"  {name:15s} → {result}")

    # Composite: and(risk<=LOW, score>=0.7, weight<=2.0)
    combined = AndCondition(conditions=[low_risk, good_score, small_weight])
    result = await ev.evaluate(combined, proposal, RiskLevel.LOW, POLICY)
    print(f"  {'AND(all three)':15s} → {result}")

    # Or with one failing
    or_node = OrCondition(
        conditions=[
            ScoreCondition(min_score=Decimal("0.99")),  # fails
            RiskLevelCondition(level=RiskLevel.MEDIUM, operator="le"),  # passes
        ]
    )
    result = await ev.evaluate(or_node, proposal, RiskLevel.LOW, POLICY)
    print(f"  {'OR(fail, pass)':15s} → {result}")

    # Not
    not_banned = NotCondition(condition=ActionCondition(actions=[ActionName.BAN], mode="allow"))
    result = await ev.evaluate(not_banned, _proposal(decision=ActionName.STATUS), RiskLevel.LOW, POLICY)
    print(f"  {'NOT(is_ban)':15s} → {result}")
    print()


# ── 2. Pluggable evaluators ────────────────────────────────────


async def demo_evaluator_plugins() -> None:
    print("=== 2. Pluggable evaluators ===\n")

    registry = EvaluatorRegistry(auto_discover=False)

    # Regex evaluator: deny resources matching a sensitive pattern
    regex_ev = RegexEvaluator(
        RegexEvaluatorConfig(patterns=[r"^(pii|secret|internal)-"], field="resource_id", deny_on_match=True)
    )
    registry.register(regex_ev)

    # List evaluator: only allow specific actions
    list_ev = ListEvaluator(
        ListEvaluatorConfig(allowlist=["status", "check_balance", "fetch_metrics"], field="decision")
    )
    registry.register(list_ev)

    print(f"  Registered evaluators: {[e.name for e in registry.all()]}")

    # Test against different proposals
    test_cases = [
        ("safe resource + allowed action", _proposal(resource_id="user-42", decision=ActionName.STATUS)),
        ("PII resource", _proposal(resource_id="pii-customer-data", decision=ActionName.STATUS)),
        ("disallowed action", _proposal(resource_id="user-42", decision=ActionName.WIRE_TRANSFER)),
    ]

    for label, proposal in test_cases:
        results = []
        for evaluator in registry.all():
            r = await evaluator.evaluate(proposal, POLICY)
            results.append((evaluator.name, r.allow, r.reason))

        all_allow = all(r[1] for r in results)
        print(f"  {label}")
        for name, allow, reason in results:
            print(f"    {name:8s}: {'ALLOW' if allow else 'DENY':5s} — {reason}")
        print(f"    overall: {'ALLOW' if all_allow else 'DENY'}")
        print()


# ── 3. Evaluator conditions in trees ───────────────────────────


async def demo_evaluator_in_tree() -> None:
    print("=== 3. Evaluator conditions in trees ===\n")

    registry = EvaluatorRegistry(auto_discover=False)
    registry.register(
        RegexEvaluator(RegexEvaluatorConfig(patterns=[r"^(pii|secret)-"], field="resource_id", deny_on_match=True))
    )
    registry.register(ListEvaluator(ListEvaluatorConfig(allowlist=["status", "check_balance"], field="decision")))

    # Build a tree: and(risk<=MEDIUM, regex_check, list_check)
    # regex denies PII resources, list allows only safe actions
    tree = AndCondition(
        conditions=[
            RiskLevelCondition(level=RiskLevel.MEDIUM, operator="le"),
            EvaluatorCondition(evaluator_name="regex"),
            EvaluatorCondition(evaluator_name="list"),
        ]
    )

    ev = ConditionEvaluator(evaluator_registry=registry)

    test_cases = [
        ("safe + allowed", _proposal(resource_id="user-42", decision=ActionName.STATUS), RiskLevel.LOW),
        ("PII resource", _proposal(resource_id="pii-data", decision=ActionName.STATUS), RiskLevel.LOW),
        ("high risk", _proposal(resource_id="user-42", decision=ActionName.STATUS), RiskLevel.HIGH),
        ("blocked action", _proposal(resource_id="user-42", decision=ActionName.WIRE_TRANSFER), RiskLevel.LOW),
    ]

    for label, proposal, risk in test_cases:
        result = await ev.evaluate(tree, proposal, risk, POLICY)
        print(f"  {label:20s} (risk={risk.value}) → {'ALLOW' if result else 'DENY'}")
    print()


# ── 4. Parallel evaluation with cancel-on-deny ─────────────────


async def demo_parallel_evaluation() -> None:
    print("=== 4. Parallel evaluation with cancel-on-deny ===\n")

    registry = EvaluatorRegistry(auto_discover=False)
    registry.register(
        RegexEvaluator(RegexEvaluatorConfig(patterns=[r"^pii-"], field="resource_id", deny_on_match=True))
    )
    registry.register(ListEvaluator(ListEvaluatorConfig(allowlist=["status", "check_balance"], field="decision")))

    parallel = ParallelPolicyEvaluator(max_concurrent=2)

    # Safe proposal — all evaluators allow
    safe = _proposal(resource_id="user-42", decision=ActionName.STATUS)
    result = await parallel.evaluate_all([lambda ev=ev: ev.evaluate(safe, POLICY) for ev in registry.all()])
    print("  Safe proposal:")
    print(f"    overall: {'ALLOW' if result.overall_allow else 'DENY'}")
    print(f"    evaluated: {len(result.results)}, cancelled: {result.cancelled_count}")
    print(f"    elapsed: {result.elapsed_ms:.1f}ms")

    # Unsafe proposal — first evaluator denies, rest should cancel
    unsafe = _proposal(resource_id="pii-customer", decision=ActionName.WIRE_TRANSFER)
    result = await parallel.evaluate_all([lambda ev=ev: ev.evaluate(unsafe, POLICY) for ev in registry.all()])
    print("\n  Unsafe proposal:")
    print(f"    overall: {'ALLOW' if result.overall_allow else 'DENY'}")
    print(f"    evaluated: {len(result.results)}, cancelled: {result.cancelled_count}")
    for r in result.results:
        print(f"    → {'ALLOW' if r.allow else 'DENY':5s} — {r.reason}")
    print()


# ── Main ─────────────────────────────────────────────────────────


async def main() -> None:
    await demo_basic_trees()
    await demo_evaluator_plugins()
    await demo_evaluator_in_tree()
    await demo_parallel_evaluation()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
