"""Lazy model registry for decoupling ORM models from engine logic."""


class ModelRegistry:
    """Registry for ORM model classes.

    Host applications register their concrete SQLAlchemy models at startup,
    and engine code resolves them lazily via ``ModelRegistry.get(name)``.
    """

    _models: dict[str, type] = {}

    @classmethod
    def register(cls, name: str, model: type) -> None:
        cls._models[name] = model

    @classmethod
    def get(cls, name: str) -> type:
        if name not in cls._models:
            raise RuntimeError(
                f"Model '{name}' not registered. "
                f"Call ModelRegistry.register('{name}', YourModel) at startup."
            )
        return cls._models[name]

    @classmethod
    def reset(cls) -> None:
        cls._models.clear()
