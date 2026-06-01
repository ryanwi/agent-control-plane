"""Deterministic benchmark protocol hooks for control-plane experiments."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Protocol

from agent_control_plane.types.benchmark import (
    BenchmarkRunResult,
    BenchmarkRunSpec,
)


@dataclass(frozen=True)
class FitnessResult:
    """Scalar fitness score and per-component breakdown from a fitness evaluation."""

    score: float
    breakdown: dict[str, float]


class ScenarioRunner(Protocol):
    def run(self, spec: BenchmarkRunSpec) -> dict[str, float]: ...


class FitnessEvaluator(Protocol):
    def evaluate(self, metrics: dict[str, float], spec: BenchmarkRunSpec) -> FitnessResult: ...


class WeightedFitnessEvaluator:
    """Default scalarization with safety-biased penalties."""

    def evaluate(self, metrics: dict[str, float], spec: BenchmarkRunSpec) -> FitnessResult:
        w = spec.weights
        throughput = metrics.get("throughput", 0.0) * w.throughput_weight
        safety_penalty = metrics.get("guardrail_denies", 0.0) * w.safety_weight
        reliability_penalty = metrics.get("rollbacks", 0.0) * w.reliability_weight
        efficiency_penalty = metrics.get("budget_denied", 0.0) * w.efficiency_weight
        score = throughput - safety_penalty - reliability_penalty - efficiency_penalty
        return FitnessResult(
            score=score,
            breakdown={
                "throughput": throughput,
                "safety_penalty": safety_penalty,
                "reliability_penalty": reliability_penalty,
                "efficiency_penalty": efficiency_penalty,
            },
        )


def hash_config(config: dict[str, object]) -> str:
    payload = repr(sorted(config.items())).encode("utf-8")
    return sha256(payload).hexdigest()[:16]


def run_benchmark(
    spec: BenchmarkRunSpec,
    *,
    runner: ScenarioRunner,
    evaluator: FitnessEvaluator | None = None,
) -> BenchmarkRunResult:
    started = datetime.now(UTC)
    metrics = runner.run(spec)
    active_evaluator = evaluator or WeightedFitnessEvaluator()
    fitness = active_evaluator.evaluate(metrics, spec)
    ended = datetime.now(UTC)
    return BenchmarkRunResult(
        scenario_name=spec.scenario.name,
        scenario_version=spec.scenario.version,
        seed=spec.scenario.seed,
        config_hash=spec.config_hash,
        metrics=metrics,
        fitness=fitness.score,
        fitness_breakdown=fitness.breakdown,
        started_at=started,
        ended_at=ended,
    )


def run_batch(
    specs: list[BenchmarkRunSpec],
    *,
    runner: ScenarioRunner,
    evaluator: FitnessEvaluator | None = None,
) -> list[BenchmarkRunResult]:
    return [run_benchmark(spec, runner=runner, evaluator=evaluator) for spec in specs]
