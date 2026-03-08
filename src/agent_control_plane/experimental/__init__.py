"""Experimental extension contracts (pre-1.0, subject to change)."""

from .capabilities import (
    CapabilityDescriptor,
    CapabilityProvider,
    CapabilitySet,
    ControlPlaneCapability,
    StaticCapabilityProvider,
    capability_set_from_mapping,
    resolve_capabilities,
)

__all__ = [
    "CapabilityDescriptor",
    "CapabilityProvider",
    "CapabilitySet",
    "ControlPlaneCapability",
    "StaticCapabilityProvider",
    "capability_set_from_mapping",
    "resolve_capabilities",
]
