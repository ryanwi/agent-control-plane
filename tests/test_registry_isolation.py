"""Tests for instance-scoped model registry behavior."""

from agent_control_plane.models.reference import register_models
from agent_control_plane.models.registry import ModelRegistry, ScopedModelRegistry, registry_scope


def test_scoped_registry_isolated_from_global():
    ModelRegistry.reset()
    scoped = ScopedModelRegistry()
    register_models(registry=scoped)

    # Global path is still empty
    try:
        ModelRegistry.get("ControlSession")
        raise AssertionError("expected global registry to be empty")
    except RuntimeError:
        pass

    # Scoped path resolves when active
    with registry_scope(scoped):
        assert ModelRegistry.get("ControlSession").__name__ == "ControlSession"

    # Scoped activation should not leak into global
    try:
        ModelRegistry.get("ControlSession")
        raise AssertionError("expected scoped registry to not leak globally")
    except RuntimeError:
        pass
