"""Turn transport: sequencing, replay, cancellation.

A turn's lifetime is decoupled from the HTTP connection that started it. That
separation is the point of this module and it buys three things:

**A dropped connection does not kill a turn.** Mobile networks and laptop lids
drop long connections routinely. Killing a half-applied turn on disconnect
would leave the document in a state the user never asked for and cannot see.

**Resumption is cheap.** Each turn keeps a bounded ring buffer of recent
events, so a reconnecting client asks for everything after the last ``seq`` it
saw and catches up, rather than re-running the model.

**Cancellation is cooperative.** The loop checks a flag between ops and after
each chunk. Hard-killing a task mid-apply could tear a batch; a checked flag
means cancellation always lands on a consistent boundary.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import AsyncIterator

from studio.streaming.events import Event, Heartbeat

logger = logging.getLogger(__name__)

# Enough to cover a reconnect during a long turn without unbounded growth.
REPLAY_BUFFER_SIZE = 500
# Turn state is kept briefly after completion so a client that reconnects late
# still receives the terminal event rather than an ambiguous 404.
RETENTION_SECONDS = 120.0
HEARTBEAT_SECONDS = 15.0


class Cancelled(Exception):
    """Raised inside the loop when the client asked to stop."""


@dataclass
class TurnChannel:
    """One turn's event stream."""

    turn_id: str
    document_id: str

    _queue: asyncio.Queue[Event | None] = field(default_factory=asyncio.Queue, init=False)
    _replay: deque[Event] = field(
        default_factory=lambda: deque(maxlen=REPLAY_BUFFER_SIZE), init=False
    )
    _seq: int = field(default=0, init=False)
    _cancelled: bool = field(default=False, init=False)
    _finished: bool = field(default=False, init=False)
    _finished_at: float | None = field(default=None, init=False)

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    @property
    def finished(self) -> bool:
        return self._finished

    def cancel(self) -> None:
        """Request a stop. The loop honours it at its next checkpoint."""
        self._cancelled = True

    def raise_if_cancelled(self) -> None:
        if self._cancelled:
            raise Cancelled()

    def emit(self, event: Event) -> None:
        """Stamp and publish an event.

        Sequencing happens here, in one place, so no producer can emit an
        out-of-order or duplicate ``seq``.
        """
        self._seq += 1
        event.seq = self._seq
        event.turn_id = self.turn_id
        self._replay.append(event)
        self._queue.put_nowait(event)

    def close(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._finished_at = time.monotonic()
        self._queue.put_nowait(None)

    def expired(self, now: float | None = None) -> bool:
        if self._finished_at is None:
            return False
        return (now or time.monotonic()) - self._finished_at > RETENTION_SECONDS

    def replay_from(self, seq: int) -> list[Event]:
        """Events after ``seq`` still held in the buffer."""
        return [event for event in self._replay if event.seq > seq]

    async def drain(self, *, from_seq: int = 0) -> AsyncIterator[Event]:
        """Yield replayed events, then live ones, until the turn ends.

        A heartbeat is emitted during silence so intermediaries do not close a
        connection while a local model is still thinking.
        """
        for event in self.replay_from(from_seq):
            yield event

        if self._finished:
            return

        while True:
            try:
                event = await asyncio.wait_for(
                    self._queue.get(), timeout=HEARTBEAT_SECONDS
                )
            except asyncio.TimeoutError:
                # Not stamped through emit(): a keepalive must not consume a
                # sequence number, or a reconnecting client sees a phantom gap.
                yield Heartbeat(turn_id=self.turn_id, seq=self._seq)
                continue

            if event is None:
                return
            yield event


class TurnRegistry:
    """Live turns, by id.

    In-process and single-node on purpose. Making this distributed means a
    shared broker, and nothing about the current deployment justifies that
    complexity; the seam is narrow enough to swap later if it ever does.
    """

    def __init__(self) -> None:
        self._turns: dict[str, TurnChannel] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def create(self, turn_id: str, document_id: str) -> TurnChannel:
        self._sweep()
        channel = TurnChannel(turn_id=turn_id, document_id=document_id)
        self._turns[turn_id] = channel
        return channel

    def get(self, turn_id: str) -> TurnChannel | None:
        return self._turns.get(turn_id)

    def attach(self, turn_id: str, task: asyncio.Task[None]) -> None:
        self._tasks[turn_id] = task

    def cancel(self, turn_id: str) -> bool:
        channel = self._turns.get(turn_id)
        if channel is None:
            return False
        channel.cancel()
        return True

    async def shutdown(self) -> None:
        """Stop every running turn. Called on application shutdown."""
        for channel in self._turns.values():
            channel.cancel()
        tasks = [task for task in self._tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._turns.clear()
        self._tasks.clear()

    def _sweep(self) -> None:
        now = time.monotonic()
        stale = [
            turn_id
            for turn_id, channel in self._turns.items()
            if channel.expired(now)
        ]
        for turn_id in stale:
            self._turns.pop(turn_id, None)
            self._tasks.pop(turn_id, None)
        if stale:
            logger.debug("Swept %s finished turns", len(stale))
