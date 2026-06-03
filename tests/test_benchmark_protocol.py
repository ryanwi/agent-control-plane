from __future__ import annotations

from uuid import UUID

from agent_control_plane.benchmark import WeightedFitnessEvaluator, hash_config, run_batch, run_benchmark
from agent_control_plane.types.benchmark import BenchmarkRunSpec, BenchmarkScenarioSpec, FitnessWeights


class _Runner:
    def run(self, spec: BenchmarkRunSpec) -> dict[str, float]:
        _ = spec
        return {
            "throughput": 10.0,
            "guardrail_denies": 2.0,
            "rollbacks": 1.0,
            "budget_denied": 0.5,
        }


def test_hash_config_is_stable_for_key_order() -> None:
    left = hash_config({"a": 1, "b": 2})
    right = hash_config({"b": 2, "a": 1})
    assert left == right


def test_run_benchmark_computes_weighted_fitness() -> None:
    spec = BenchmarkRunSpec(
        scenario=BenchmarkScenarioSpec(name="base", version="1", seed=7),
        config={"lr": 0.001},
        config_hash=hash_config({"lr": 0.001}),
        weights=FitnessWeights(
            throughput_weight=1.0,
            safety_weight=2.0,
            reliability_weight=3.0,
            efficiency_weight=4.0,
        ),
    )

    result = run_benchmark(spec, runner=_Runner(), evaluator=WeightedFitnessEvaluator())

    expected = 10.0 - (2.0 * 2.0) - (1.0 * 3.0) - (0.5 * 4.0)
    assert result.fitness == expected
    assert result.metrics["throughput"] == 10.0
    assert result.fitness_breakdown["safety_penalty"] == 4.0


def test_run_batch_executes_all_specs() -> None:
    specs = [
        BenchmarkRunSpec(
            scenario=BenchmarkScenarioSpec(name=f"scenario-{idx}", version="1", seed=idx),
            config={"lr": 0.001 * (idx + 1)},
            config_hash=hash_config({"lr": 0.001 * (idx + 1)}),
        )
        for idx in range(3)
    ]

    results = run_batch(specs, runner=_Runner())
    assert len(results) == 3
    assert [r.scenario_name for r in results] == ["scenario-0", "scenario-1", "scenario-2"]


def test_benchmark_run_result_shape() -> None:
    spec = BenchmarkRunSpec(
        scenario=BenchmarkScenarioSpec(name="shape-check", version="2", seed=42),
        config={"x": 1},
        config_hash=hash_config({"x": 1}),
    )
    result = run_benchmark(spec, runner=_Runner())

    assert isinstance(result.run_id, UUID)
    assert result.scenario_name == "shape-check"
    assert result.scenario_version == "2"
    assert result.seed == 42
    assert isinstance(result.config_hash, str) and len(result.config_hash) == 16
    assert isinstance(result.metrics, dict)
    assert isinstance(result.fitness, float)
    assert isinstance(result.fitness_breakdown, dict)
    assert result.started_at.tzinfo is not None  # UTC-aware
    assert result.ended_at.tzinfo is not None
    assert result.ended_at >= result.started_at
    assert isinstance(result.notes, list)
