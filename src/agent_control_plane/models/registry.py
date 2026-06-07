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
    def get(self, name: str) -> type[Any]: ...
    def reset(self) -> None: ...
    def models(self) -> dict[str, type]: ...


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
    def get(cls, name: str) -> type[Any]:
        scoped = cls._scoped_models.get()
        model_map = scoped if scoped is not None else cls._models
        if name not in model_map:
            raise RuntimeError(
                f"Model '{name}' not registered. Call ModelRegistry.register('{name}', YourModel) at startup."
            )
        return model_map[name]

    @classmethod
    def reset(cls) -> None:
        cls._models.clear()

    @classmethod
    def has_models(cls) -> bool:
        """Return True if any models are registered in the global registry."""
        return bool(cls._models)

    @classmethod
    def models(cls) -> dict[str, type]:
        """Return the current model map (scoped or global)."""
        scoped = cls._scoped_models.get()
        return scoped if scoped is not None else cls._models

    @classmethod
    @contextmanager
    def scope(cls, registry: RegistryProtocol) -> Iterator[None]:
        """Temporarily scope ModelRegistry.get() to an explicit registry instance."""
        if registry is cls:
            yield
            return
        model_map = registry.models()
        if not model_map:
            yield
            return
        token = cls._scoped_models.set(model_map)
        try:
            yield
        finally:
            cls._scoped_models.reset(token)


class ScopedModelRegistry:
    """Instance-scoped model registry for integration isolation."""

    def __init__(self) -> None:
        self._model_map: dict[str, type] = {}

    def register(self, name: str, model: type) -> None:
        self._model_map[name] = model

    def get(self, name: str) -> type[Any]:
        if name not in self._model_map:
            raise RuntimeError(
                f"Model '{name}' not registered. Call registry.register('{name}', YourModel) at startup."
            )
        return self._model_map[name]

    def reset(self) -> None:
        self._model_map.clear()

    def models(self) -> dict[str, type]:
        """Return all registered models."""
        return self._model_map


DEFAULT_MODEL_REGISTRY: RegistryProtocol = ModelRegistry


@contextmanager
def registry_scope(registry: RegistryProtocol) -> Iterator[None]:
    """Temporarily scope ModelRegistry.get() to an explicit registry instance."""
    with ModelRegistry.scope(registry):
        yield
