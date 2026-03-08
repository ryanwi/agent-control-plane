"""Benchmark protocol DTOs for deterministic control-plane experiments."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class FitnessWeights(BaseModel):
    throughput_weight: float = 1.0
    safety_weight: float = 1.0
    reliability_weight: float = 1.0
    efficiency_weight: float = 1.0


class BenchmarkScenarioSpec(BaseModel):
    name: str
    version: str = "1"
    seed: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class BenchmarkRunSpec(BaseModel):
    scenario: BenchmarkScenarioSpec
    config: dict[str, Any] = Field(default_factory=dict)
    config_hash: str
    weights: FitnessWeights = Field(default_factory=FitnessWeights)


class BenchmarkRunResultDTO(BaseModel):
    run_id: UUID = Field(default_factory=uuid4)
    scenario_name: str
    scenario_version: str
    seed: int
    config_hash: str
    metrics: dict[str, float] = Field(default_factory=dict)
    fitness: float
    fitness_breakdown: dict[str, float] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ended_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    notes: list[str] = Field(default_factory=list)
