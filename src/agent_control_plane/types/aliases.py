"""Global alias profile support for DTO input/output mapping."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from pydantic import BaseModel


@dataclass(frozen=True)
class FieldAliasMap:
    """Canonical->alias mapping for DTO fields."""

    canonical_to_alias: dict[str, str]

    @property
    def alias_to_canonical(self) -> dict[str, str]:
        return {alias: canonical for canonical, alias in self.canonical_to_alias.items()}


@dataclass(frozen=True)
class AliasProfile:
    """Named alias profile used across DTOs."""

    name: str
    aliases: FieldAliasMap


class AliasRegistry:
    """In-memory registry for alias profiles."""

    _profiles: ClassVar[dict[str, AliasProfile]] = {}

    @classmethod
    def register_profile(cls, profile: AliasProfile) -> None:
        cls._profiles[profile.name] = profile

    @classmethod
    def get_profile(cls, profile: str | AliasProfile) -> AliasProfile:
        if isinstance(profile, AliasProfile):
            return profile
        if profile not in cls._profiles:
            raise ValueError(f"Alias profile not registered: {profile}")
        return cls._profiles[profile]

    @classmethod
    def clear_profiles(cls) -> None:
        cls._profiles.clear()


def apply_inbound_aliases(data: Any, profile: str | AliasProfile) -> Any:
    """Convert aliased payload keys into canonical DTO field names."""
    resolved = AliasRegistry.get_profile(profile)
    return _apply_inbound_aliases(data, resolved)


def apply_outbound_aliases(data: Any, profile: str | AliasProfile) -> Any:
    """Convert canonical DTO field names into profile-specific aliases."""
    resolved = AliasRegistry.get_profile(profile)
    return _apply_outbound_aliases(data, resolved)


def _apply_inbound_aliases(data: Any, profile: AliasProfile) -> Any:
    if isinstance(data, BaseModel):
        data = data.model_dump(mode="python")
    if isinstance(data, Mapping):
        converted: dict[str, Any] = {}
        alias_to_canonical = profile.aliases.alias_to_canonical
        for key, value in data.items():
            normalized_key = key if isinstance(key, str) else str(key)
            canonical_key = alias_to_canonical.get(normalized_key, normalized_key)
            converted[canonical_key] = _apply_inbound_aliases(value, profile)
        return converted
    if isinstance(data, list):
        return [_apply_inbound_aliases(item, profile) for item in data]
    return data


def _apply_outbound_aliases(data: Any, profile: AliasProfile) -> Any:
    if isinstance(data, Mapping):
        converted: dict[str, Any] = {}
        canonical_to_alias = profile.aliases.canonical_to_alias
        for key, value in data.items():
            normalized_key = key if isinstance(key, str) else str(key)
            alias_key = canonical_to_alias.get(normalized_key, normalized_key)
            converted[alias_key] = _apply_outbound_aliases(value, profile)
        return converted
    if isinstance(data, list):
        return [_apply_outbound_aliases(item, profile) for item in data]
    return data


class AliasProfiledModel(BaseModel):
    """Base model with profile-aware validate/dump helpers."""

    @classmethod
    def model_validate_with_profile(
        cls,
        data: Any,
        *,
        profile: str | AliasProfile | None = None,
    ) -> Any:
        if profile is None:
            return cls.model_validate(data)
        normalized = apply_inbound_aliases(data, profile)
        return cls.model_validate(normalized)

    def model_dump_with_profile(
        self,
        *,
        profile: str | AliasProfile | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        data = self.model_dump(**kwargs)
        if profile is None:
            return data
        converted = apply_outbound_aliases(data, profile)
        if not isinstance(converted, dict):
            raise TypeError("Profiled dump produced non-dict payload")
        return converted
