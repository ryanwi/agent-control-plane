"""Experimental capability contracts for optional integration layers."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, Field


class ControlPlaneCapability(StrEnum):
    """Known optional capability identifiers for external layers."""

    FLEET_MANAGEMENT = "fleet_management"
    ENTERPRISE_IDENTITY = "enterprise_identity"
    COMPLIANCE_REPORTING = "compliance_reporting"
    MANAGED_OPERATIONS = "managed_operations"


class CapabilityDescriptor(BaseModel):
    """Descriptor for one optional capability."""

    name: str
    version: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class CapabilitySet(BaseModel):
    """Collection of capability descriptors resolved at runtime."""

    items: list[CapabilityDescriptor] = Field(default_factory=list)

    def has(self, capability: ControlPlaneCapability | str) -> bool:
        target = capability.value if isinstance(capability, ControlPlaneCapability) else capability
        return any(item.name == target for item in self.items)


class CapabilityProvider(Protocol):
    """Runtime provider for optional non-core capabilities."""

    def list_capabilities(self) -> CapabilitySet: ...


class StaticCapabilityProvider:
    """Simple static provider for app/runtime composition."""

    def __init__(self, capabilities: CapabilitySet | list[CapabilityDescriptor]) -> None:
        self._capabilities = (
            capabilities if isinstance(capabilities, CapabilitySet) else CapabilitySet(items=capabilities)
        )

    def list_capabilities(self) -> CapabilitySet:
        return self._capabilities.model_copy(deep=True)


def capability_set_from_mapping(mapping: Mapping[str, Mapping[str, Any] | None]) -> CapabilitySet:
    """Build a capability set from `name -> config` mapping."""
    items: list[CapabilityDescriptor] = []
    for name, config in mapping.items():
        details = dict(config or {})
        version = details.pop("version", None)
        if version is not None and not isinstance(version, str):
            raise TypeError(f"Capability {name} has non-string version")
        items.append(CapabilityDescriptor(name=name, version=version, metadata=details))
    return CapabilitySet(items=items)


def resolve_capabilities(provider: CapabilityProvider | None) -> CapabilitySet:
    """Resolve capabilities from provider, defaulting to an empty set."""
    if provider is None:
        return CapabilitySet()
    return provider.list_capabilities()
