"""Lazy model registry for decoupling ORM models from engine logic."""

from typing import Any, ClassVar


class ModelRegistry:
    """Registry for ORM model classes.

    Host applications register their concrete SQLAlchemy models at startup,
    and engine code resolves them lazily via ``ModelRegistry.get(name)``.
    """

    _models: ClassVar[dict[str, type]] = {}

    @classmethod
    def register(cls, name: str, model: type) -> None:
        cls._models[name] = model

    @classmethod
    def get(cls, name: str) -> Any:
        if name not in cls._models:
            raise RuntimeError(
                f"Model '{name}' not registered. Call ModelRegistry.register('{name}', YourModel) at startup."
            )
        return cls._models[name]

    @classmethod
    def reset(cls) -> None:
        cls._models.clear()
