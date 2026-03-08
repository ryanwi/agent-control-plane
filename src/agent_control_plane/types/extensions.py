"""Schema extension registries for typed DTO substructures."""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

TModel = TypeVar("TModel", bound=BaseModel)

_METADATA_SCHEMAS: dict[type[BaseModel], type[BaseModel]] = {}
_RISK_LIMITS_EXTENSION_SCHEMA: type[BaseModel] | None = None


def register_metadata_schema(dto_type: type[BaseModel], schema: type[TModel]) -> None:
    """Register a metadata schema for a DTO type."""
    _METADATA_SCHEMAS[dto_type] = schema


def get_metadata_schema(dto_type: type[BaseModel]) -> type[BaseModel] | None:
    """Return registered metadata schema for a DTO type."""
    return _METADATA_SCHEMAS.get(dto_type)


def clear_metadata_schemas() -> None:
    """Clear metadata schema registrations (primarily for tests)."""
    _METADATA_SCHEMAS.clear()


def register_risk_limits_extension_schema(schema: type[TModel]) -> None:
    """Register the extension schema for RiskLimits.custom values."""
    global _RISK_LIMITS_EXTENSION_SCHEMA
    _RISK_LIMITS_EXTENSION_SCHEMA = schema


def get_risk_limits_extension_schema() -> type[BaseModel] | None:
    """Return registered RiskLimits extension schema."""
    return _RISK_LIMITS_EXTENSION_SCHEMA


def clear_risk_limits_extension_schema() -> None:
    """Clear RiskLimits extension schema (primarily for tests)."""
    global _RISK_LIMITS_EXTENSION_SCHEMA
    _RISK_LIMITS_EXTENSION_SCHEMA = None
