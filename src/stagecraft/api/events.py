"""Per-session event bus: a bounded log plus live broadcast, ordered by ``seq``.

This is the in-process shape of a Redis Streams + Pub/Sub design::

    Redis                          here
    INCR   <session>:seq     ->    per-session counter
    XADD   <session> MAXLEN  ->    append to a bounded deque (the log)
    PUBLISH <session>        ->    push onto every live subscriber's queue
    XREVRANGE COUNT n        ->    read the tail of the log (replay)

Allocating the seq, appending and broadcasting happen in one critical section, the
way a Lua script makes them one round trip in Redis. So seq order, log order and
delivery order can never disagree.

Subscribing is ordered on purpose: **register, then replay, then drain**. An event
published while the replay is being read lands in the subscriber's queue as well as
in the log, so nothing falls into the gap; the subscriber remembers the last seq it
delivered and drops the second copy, so nothing arrives twice.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class Event:
    session_id: str
    seq: int
    type: str
    data: dict[str, Any]
    created_at: str

    def to_sse(self) -> str:
        payload = json.dumps(
            {"seq": self.seq, "created_at": self.created_at, **self.data}, ensure_ascii=False
        )
        return f"id: {self.seq}\nevent: {self.type}\ndata: {payload}\n\n"


_Subscriber = tuple[asyncio.AbstractEventLoop, "asyncio.Queue[Event]"]


class EventBus:
    def __init__(self, *, log_size: int = 300, replay_limit: int = 50) -> None:
        self.log_size = log_size
        self.replay_limit = replay_limit
        self._logs: dict[str, deque[Event]] = defaultdict(lambda: deque(maxlen=log_size))
        self._seqs: dict[str, int] = defaultdict(int)
        self._subscribers: dict[str, set[_Subscriber]] = defaultdict(set)
        self._lock = threading.Lock()

    def publish(self, session_id: str, type: str, data: dict[str, Any]) -> Event:
        with self._lock:
            seq = self._seqs[session_id] + 1
            self._seqs[session_id] = seq
            event = Event(
                session_id=session_id,
                seq=seq,
                type=type,
                data=data,
                created_at=datetime.now(UTC).isoformat(),
            )
            self._logs[session_id].append(event)
            subscribers = list(self._subscribers[session_id])
            # Enqueue inside the lock: two publishers must not interleave deliveries.
            for loop, queue in subscribers:
                _enqueue(loop, queue, event)
        return event

    async def read_log(self, session_id: str, *, after_seq: int | None, limit: int) -> list[Event]:
        """The replay read. Async because in the Redis version it is a round trip."""
        await asyncio.sleep(0)
        with self._lock:
            log = list(self._logs[session_id])
        if after_seq is not None:
            return [event for event in log if event.seq > after_seq]
        return log[-limit:] if limit else []

    async def subscribe(
        self,
        session_id: str,
        *,
        after_seq: int | None = None,
        heartbeat: float | None = None,
    ) -> AsyncIterator[Event | None]:
        """Replay, then follow live. Yields ``None`` as a heartbeat when idle.

        With ``after_seq`` (an SSE ``Last-Event-ID``) the replay is everything newer than
        that seq. If the log has already dropped some of those, a ``resync`` event tells
        the client to reload the snapshot endpoint rather than trust a gap.
        """
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Event] = asyncio.Queue()
        entry: _Subscriber = (loop, queue)
        with self._lock:
            self._subscribers[session_id].add(entry)
        try:
            last = after_seq or 0
            replay = await self.read_log(session_id, after_seq=after_seq, limit=self.replay_limit)
            if after_seq is not None and replay and replay[0].seq > after_seq + 1:
                yield Event(
                    session_id=session_id,
                    seq=after_seq,
                    type="resync",
                    data={"reason": "events were trimmed from the log", "from_seq": replay[0].seq},
                    created_at=datetime.now(UTC).isoformat(),
                )
            for event in replay:
                if event.seq > last:
                    last = event.seq
                    yield event
            while True:
                try:
                    if heartbeat is None:
                        event = await queue.get()
                    else:
                        event = await asyncio.wait_for(queue.get(), heartbeat)
                except TimeoutError:
                    yield None
                    continue
                if event.seq <= last:
                    continue
                last = event.seq
                yield event
        finally:
            with self._lock:
                self._subscribers[session_id].discard(entry)

    def subscriber_count(self, session_id: str) -> int:
        with self._lock:
            return len(self._subscribers[session_id])

    def last_seq(self, session_id: str) -> int:
        with self._lock:
            return self._seqs[session_id]


def _enqueue(loop: asyncio.AbstractEventLoop, queue: asyncio.Queue[Event], event: Event) -> None:
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is loop:
        queue.put_nowait(event)
    else:
        loop.call_soon_threadsafe(queue.put_nowait, event)
