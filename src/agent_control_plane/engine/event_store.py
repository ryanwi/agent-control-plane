"""Append-only event persistence with monotonic sequencing per session."""

import logging
from collections import deque
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_control_plane.models.registry import ModelRegistry

logger = logging.getLogger(__name__)

# Max buffered telemetry events during DB outage
_MAX_BUFFER_SIZE = 1000


class EventStore:
    """Append-only event store with fail-closed/buffer-ok semantics."""

    def __init__(self) -> None:
        self._buffer: deque[dict[str, Any]] = deque(maxlen=_MAX_BUFFER_SIZE)

    async def append(
        self,
        session: AsyncSession,
        session_id: UUID,
        event_kind: str,
        payload: dict[str, Any],
        *,
        state_bearing: bool = False,
        agent_id: str | None = None,
        correlation_id: UUID | None = None,
        routing_decision: dict[str, Any] | None = None,
        routing_reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> Any | None:
        """Append an event with monotonic sequence allocation.

        Args:
            session: Active database session (caller manages transaction).
            session_id: The control session this event belongs to.
            event_kind: Type of event (from EventKind enum).
            payload: Event-specific data.
            state_bearing: If True, DB write failure raises (fail-closed).
                If False, event is buffered on failure (telemetry).
            agent_id: Optional agent that generated this event.
            correlation_id: Optional correlation for request tracing.
            routing_decision: Optional routing audit data.
            routing_reason: Optional human-readable routing explanation.
            idempotency_key: Optional dedup key for cycle_started events.

        Returns:
            The persisted ControlEvent.

        Raises:
            OperationalError: On DB failure when state_bearing=True.
        """
        try:
            seq = await self._allocate_seq(session, session_id)
            ControlEvent = ModelRegistry.get("ControlEvent")
            event = ControlEvent(
                id=uuid4(),
                session_id=session_id,
                seq=seq,
                event_kind=event_kind,
                agent_id=agent_id,
                correlation_id=correlation_id,
                payload=payload,
                routing_decision=routing_decision,
                routing_reason=routing_reason,
                idempotency_key=idempotency_key,
            )
            session.add(event)
            await session.flush()
            return event
        except OperationalError:
            if state_bearing:
                raise
            self._buffer.append(
                {
                    "session_id": session_id,
                    "event_kind": event_kind,
                    "payload": payload,
                    "agent_id": agent_id,
                    "correlation_id": correlation_id,
                    "buffered_at": datetime.now(UTC).isoformat(),
                }
            )
            logger.warning("Event buffered due to DB outage: %s", event_kind)
            return None
        except RuntimeError:
            if state_bearing:
                raise
            self._buffer.append(
                {
                    "session_id": session_id,
                    "event_kind": event_kind,
                    "payload": payload,
                    "agent_id": agent_id,
                    "correlation_id": correlation_id,
                    "buffered_at": datetime.now(UTC).isoformat(),
                }
            )
            logger.warning("Event buffered due to missing model registry: %s", event_kind)
            return None

    async def _allocate_seq(self, session: AsyncSession, session_id: UUID) -> int:
        """Atomically allocate the next sequence number for a session.

        Uses SELECT ... FOR UPDATE to prevent race conditions.
        """
        SessionSeqCounter = ModelRegistry.get("SessionSeqCounter")
        result = await session.execute(
            select(SessionSeqCounter)
            .where(SessionSeqCounter.session_id == session_id)
            .with_for_update()
        )
        counter = result.scalar_one()
        allocated = counter.next_seq
        await session.execute(
            update(SessionSeqCounter)
            .where(SessionSeqCounter.session_id == session_id)
            .values(next_seq=SessionSeqCounter.next_seq + 1)
        )
        return allocated

    async def replay(
        self,
        session: AsyncSession,
        session_id: UUID,
        after_seq: int = 0,
        limit: int = 100,
    ) -> list[Any]:
        """Replay events for a session after a given sequence number."""
        ControlEvent = ModelRegistry.get("ControlEvent")
        result = await session.execute(
            select(ControlEvent)
            .where(
                ControlEvent.session_id == session_id,
                ControlEvent.seq > after_seq,
            )
            .order_by(ControlEvent.seq)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def flush_buffer(self, session: AsyncSession) -> int:
        """Flush buffered telemetry events to the database.

        Returns the number of events flushed.
        """
        flushed = 0
        while self._buffer:
            item = self._buffer.popleft()
            try:
                await self.append(
                    session,
                    session_id=item["session_id"],
                    event_kind=item["event_kind"],
                    payload=item["payload"],
                    agent_id=item.get("agent_id"),
                    correlation_id=item.get("correlation_id"),
                    state_bearing=False,
                )
                flushed += 1
            except OperationalError:
                # Put it back and stop trying
                self._buffer.appendleft(item)
                break
        if flushed:
            logger.info("Flushed %d buffered events", flushed)
        return flushed

    @property
    def buffer_size(self) -> int:
        """Number of events currently buffered."""
        return len(self._buffer)
