"""Steering context for corrective agent guidance."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from .aliases import AliasProfiledModel
from .enums import ActionValue


class SteeringContext(AliasProfiledModel):
    """Corrective guidance returned when a proposal is steered instead of blocked."""

    guidance: str
    suggested_actions: list[ActionValue] = Field(default_factory=list)
    max_retries: int = 3
    metadata: dict[str, Any] = Field(default_factory=dict)
