"""Deterministic benchmark protocol hooks for control-plane experiments."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import Protocol

from agent_control_plane.types.benchmark import (
    BenchmarkRunResultDTO,
    BenchmarkRunSpec,
)


class ScenarioRunner(Protocol):
    def run(self, spec: BenchmarkRunSpec) -> dict[str, float]: ...


class FitnessEvaluator(Protocol):
    def evaluate(self, metrics: dict[str, float], spec: BenchmarkRunSpec) -> tuple[float, dict[str, float]]: ...


class WeightedFitnessEvaluator:
    """Default scalarization with safety-biased penalties."""

    def evaluate(self, metrics: dict[str, float], spec: BenchmarkRunSpec) -> tuple[float, dict[str, float]]:
        w = spec.weights
        throughput = metrics.get("throughput", 0.0) * w.throughput_weight
        safety_penalty = metrics.get("guardrail_denies", 0.0) * w.safety_weight
        reliability_penalty = metrics.get("rollbacks", 0.0) * w.reliability_weight
        efficiency_penalty = metrics.get("budget_denied", 0.0) * w.efficiency_weight
        fitness = throughput - safety_penalty - reliability_penalty - efficiency_penalty
        return (
            fitness,
            {
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
) -> BenchmarkRunResultDTO:
    started = datetime.now(UTC)
    metrics = runner.run(spec)
    active_evaluator = evaluator or WeightedFitnessEvaluator()
    fitness, breakdown = active_evaluator.evaluate(metrics, spec)
    ended = datetime.now(UTC)
    return BenchmarkRunResultDTO(
        scenario_name=spec.scenario.name,
        scenario_version=spec.scenario.version,
        seed=spec.scenario.seed,
        config_hash=spec.config_hash,
        metrics=metrics,
        fitness=fitness,
        fitness_breakdown=breakdown,
        started_at=started,
        ended_at=ended,
    )


def run_batch(
    specs: list[BenchmarkRunSpec],
    *,
    runner: ScenarioRunner,
    evaluator: FitnessEvaluator | None = None,
) -> list[BenchmarkRunResultDTO]:
    return [run_benchmark(spec, runner=runner, evaluator=evaluator) for spec in specs]
