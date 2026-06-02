"""Append-only event persistence with monotonic sequencing per session."""

from __future__ import annotations

import logging
from collections import deque
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from agent_control_plane.types.enums import EventKind
from agent_control_plane.types.frames import EventMetadata

if TYPE_CHECKING:
    from agent_control_plane.storage.protocols import AsyncEventRepository

logger = logging.getLogger(__name__)

# Max buffered telemetry events during DB outage
_MAX_BUFFER_SIZE = 1000


class EventStore:
    """Append-only event store with fail-closed/buffer-ok semantics."""

    def __init__(self, event_repo: AsyncEventRepository) -> None:
        self._repo = event_repo
        self._buffer: deque[dict[str, Any]] = deque(maxlen=_MAX_BUFFER_SIZE)

    async def append(
        self,
        session_id: UUID,
        event_kind: EventKind,
        payload: dict[str, Any],
        *,
        state_bearing: bool = False,
        metadata: EventMetadata | None = None,
    ) -> int | None:
        """Append an event with monotonic sequence allocation.

        Returns the sequence number on success, None if buffered.

        Raises on DB failure when state_bearing=True (fail-closed).
        """
        try:
            seq = await self._repo.append(
                session_id,
                event_kind,
                payload,
                state_bearing=state_bearing,
                metadata=metadata,
            )
            return seq
        except Exception:
            if state_bearing:
                raise
            m = metadata or EventMetadata()
            self._buffer.append(
                {
                    "session_id": session_id,
                    "event_kind": event_kind,
                    "payload": payload,
                    "metadata": m,
                    "buffered_at": datetime.now(UTC).isoformat(),
                }
            )
            logger.warning("Event buffered due to storage failure: %s", event_kind)
            return None

    async def replay(
        self,
        session_id: UUID,
        after_seq: int = 0,
        limit: int = 100,
    ) -> list[Any]:
        """Replay events for a session after a given sequence number."""
        return await self._repo.replay(session_id, after_seq=after_seq, limit=limit)

    async def flush_buffer(self) -> int:
        """Flush buffered telemetry events to the repository.

        Returns the number of events flushed.
        """
        flushed = 0
        while self._buffer:
            item = self._buffer.popleft()
            try:
                await self._repo.append(
                    item["session_id"],
                    item["event_kind"],
                    item["payload"],
                    state_bearing=False,
                    metadata=item.get("metadata"),
                )
                flushed += 1
            except Exception:
                self._buffer.appendleft(item)
                break
        if flushed:
            logger.info("Flushed %d buffered events", flushed)
        return flushed

    @property
    def buffer_size(self) -> int:
        """Number of events currently buffered."""
        return len(self._buffer)
