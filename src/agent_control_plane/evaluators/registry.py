"""Evaluator registry with entry-point discovery."""

from __future__ import annotations

import importlib.metadata
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .protocol import Evaluator

logger = logging.getLogger(__name__)

_ENTRY_POINT_GROUP = "agent_control_plane.evaluators"


class EvaluatorRegistry:
    """Discovers and caches evaluator instances."""

    def __init__(self, *, auto_discover: bool = True) -> None:
        self._instances: dict[str, Evaluator] = {}
        if auto_discover:
            self._discover()

    def register(self, evaluator: Evaluator) -> None:
        """Register an evaluator instance. Raises ValueError on duplicate name."""
        if evaluator.name in self._instances:
            raise ValueError(f"Evaluator already registered: {evaluator.name}")
        self._instances[evaluator.name] = evaluator

    def get(self, name: str) -> Evaluator | None:
        """Look up an evaluator by name."""
        return self._instances.get(name)

    def all(self) -> list[Evaluator]:
        """Return all registered evaluators."""
        return list(self._instances.values())

    def _discover(self) -> None:
        """Load evaluators from installed entry points.

        Entry-point evaluators that share a name with an already-registered evaluator
        are silently skipped with a warning.  This prevents a malicious or misconfigured
        plugin from overriding a built-in evaluator by claiming the same name.
        """
        group_eps = importlib.metadata.entry_points(group=_ENTRY_POINT_GROUP)
        for ep in group_eps:
            try:
                factory = ep.load()
                evaluator = factory()
                if evaluator.name in self._instances:
                    logger.warning(
                        "Entry-point evaluator %r claims name %r which is already registered; skipping.",
                        ep.name,
                        evaluator.name,
                    )
                    continue
                self._instances[evaluator.name] = evaluator
                logger.debug("Discovered evaluator: %s", evaluator.name)
            except Exception:
                logger.warning("Failed to load evaluator entry point: %s", ep.name, exc_info=True)
