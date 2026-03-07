"""Typed ID aliases for stronger API contracts."""

from typing import NewType

AgentId = NewType("AgentId", str)
ResourceId = NewType("ResourceId", str)
IdempotencyKey = NewType("IdempotencyKey", str)
