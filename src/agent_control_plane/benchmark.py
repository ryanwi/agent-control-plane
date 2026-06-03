"""Deterministic benchmark protocol hooks for control-plane experiments."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from html import escape
from pathlib import Path
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


def write_html_report(results: list[BenchmarkRunResult], path: str | Path) -> Path:
    """Write a self-contained HTML report for a list of BenchmarkRunResults."""
    out = Path(path)

    if results:
        min_start = min(r.started_at for r in results)
        max_end = max(r.ended_at for r in results)
        avg_fitness = sum(r.fitness for r in results) / len(results)
        date_range = f"{min_start.strftime('%Y-%m-%d %H:%M:%S')} UTC - {max_end.strftime('%Y-%m-%d %H:%M:%S')} UTC"
    else:
        date_range = "-"
        avg_fitness = 0.0

    def _kv_rows(d: dict[str, float]) -> str:
        return "".join(f"<tr><td>{escape(k)}</td><td>{v:.4f}</td></tr>" for k, v in sorted(d.items()))

    detail_sections = []
    summary_rows = []
    for r in results:
        duration_ms = (r.ended_at - r.started_at).total_seconds() * 1000
        summary_rows.append(
            f"<tr>"
            f"<td>{escape(r.scenario_name)}</td>"
            f"<td>{escape(r.scenario_version)}</td>"
            f"<td>{r.seed}</td>"
            f"<td><code>{escape(r.config_hash)}</code></td>"
            f"<td>{r.fitness:.4f}</td>"
            f"<td>{duration_ms:.1f}</td>"
            f"</tr>"
        )
        notes_html = (
            "<ul>" + "".join(f"<li>{escape(n)}</li>" for n in r.notes) + "</ul>" if r.notes else "<em>none</em>"
        )
        detail_sections.append(
            f"<details>"
            f"<summary>{escape(r.scenario_name)} v{escape(r.scenario_version)} "
            f"(seed={r.seed}, fitness={r.fitness:.4f})</summary>"
            f"<p><strong>run_id:</strong> <code>{escape(str(r.run_id))}</code></p>"
            f"<h4>Metrics</h4>"
            f"<table><thead><tr><th>Key</th><th>Value</th></tr></thead>"
            f"<tbody>{_kv_rows(r.metrics)}</tbody></table>"
            f"<h4>Fitness breakdown</h4>"
            f"<table><thead><tr><th>Component</th><th>Value</th></tr></thead>"
            f"<tbody>{_kv_rows(r.fitness_breakdown)}</tbody></table>"
            f"<h4>Notes</h4>{notes_html}"
            f"</details>"
        )

    summary_table = (
        "<table>"
        "<thead><tr>"
        "<th>Scenario</th><th>Version</th><th>Seed</th>"
        "<th>Config hash</th><th>Fitness</th><th>Duration (ms)</th>"
        "</tr></thead>"
        f"<tbody>{''.join(summary_rows)}</tbody>"
        "</table>"
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Benchmark Report</title>
<style>
  body {{ font-family: sans-serif; max-width: 960px; margin: 2rem auto; color: #222; }}
  h1 {{ font-size: 1.4rem; }}
  h4 {{ margin: 0.75rem 0 0.25rem; }}
  table {{ border-collapse: collapse; width: 100%; margin-bottom: 1rem; }}
  th, td {{ text-align: left; padding: 0.3rem 0.6rem; border: 1px solid #ccc; }}
  th {{ background: #f0f0f0; }}
  details {{ border: 1px solid #ddd; border-radius: 4px; padding: 0.5rem 1rem; margin-bottom: 0.5rem; }}
  summary {{ cursor: pointer; font-weight: bold; }}
  code {{ background: #f5f5f5; padding: 0.1rem 0.3rem; border-radius: 3px; }}
</style>
</head>
<body>
<h1>Benchmark Report</h1>
<p><strong>Runs:</strong> {len(results)} &nbsp;
<strong>Avg fitness:</strong> {avg_fitness:.4f} &nbsp;
<strong>Period:</strong> {escape(date_range)}</p>
<h2>Summary</h2>
{summary_table}
<h2>Details</h2>
{"".join(detail_sections) if detail_sections else "<p>No results.</p>"}
</body>
</html>"""

    out.write_text(html, encoding="utf-8")
    return out
