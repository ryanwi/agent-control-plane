"""RunHandle — the value yielded by cp.run() context managers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID


@dataclass
class RunHandle:
    """Handle for an in-progress tracked agent run.

    Yielded by ``cp.run("name")`` context managers. Collects tags that are
    written into the session's close payload so they appear in the audit log.
    """

    session_id: UUID
    _tags: dict[str, Any] = field(default_factory=dict, init=False, repr=False)

    def tag(self, **metadata: Any) -> None:
        """Attach key/value metadata to this run (accumulated until close)."""
        self._tags.update(metadata)
