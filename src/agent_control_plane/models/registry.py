"""Lazy model registry for decoupling ORM models from engine logic."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, ClassVar, Protocol, runtime_checkable


@runtime_checkable
class RegistryProtocol(Protocol):
    """Protocol for model registry implementations."""

    def register(self, name: str, model: type) -> None: ...
    def get(self, name: str) -> Any: ...
    def reset(self) -> None: ...


class ModelRegistry:
    """Global model registry (default)."""

    _models: ClassVar[dict[str, type]] = {}
    _scoped_models: ClassVar[ContextVar[dict[str, type] | None]] = ContextVar(
        "agent_control_plane_scoped_models",
        default=None,
    )

    @classmethod
    def register(cls, name: str, model: type) -> None:
        cls._models[name] = model

    @classmethod
    def get(cls, name: str) -> Any:
        models = cls._scoped_models.get() or cls._models
        if name not in models:
            raise RuntimeError(
                f"Model '{name}' not registered. Call ModelRegistry.register('{name}', YourModel) at startup."
            )
        return models[name]

    @classmethod
    def reset(cls) -> None:
        cls._models.clear()


class ScopedModelRegistry:
    """Instance-scoped model registry for integration isolation."""

    def __init__(self) -> None:
        self._models: dict[str, type] = {}

    def register(self, name: str, model: type) -> None:
        self._models[name] = model

    def get(self, name: str) -> Any:
        if name not in self._models:
            raise RuntimeError(
                f"Model '{name}' not registered. Call registry.register('{name}', YourModel) at startup."
            )
        return self._models[name]

    def reset(self) -> None:
        self._models.clear()


DEFAULT_MODEL_REGISTRY: RegistryProtocol = ModelRegistry


@contextmanager
def registry_scope(registry: RegistryProtocol) -> Iterator[None]:
    """Temporarily scope ModelRegistry.get() to an explicit registry instance."""
    if registry is ModelRegistry:
        yield
        return
    models = getattr(registry, "_models", None)
    if not isinstance(models, dict):
        yield
        return
    token = ModelRegistry._scoped_models.set(models)
    try:
        yield
    finally:
        ModelRegistry._scoped_models.reset(token)
