"""In-process event bus for SSE + WebSocket streams.

Every runtime event of interest (log emission, tool invocation, scheduler
tick, cost record) fans out through this bus. Consumers subscribe with an
async queue; the bus drops events if a consumer is slow (never blocks).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Event:
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)


class EventBus:
    def __init__(self, *, queue_size: int = 256) -> None:
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._queue_size = queue_size
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=self._queue_size)
        async with self._lock:
            self._subscribers.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue[Event]) -> None:
        async with self._lock:
            self._subscribers.discard(q)

    def publish(self, kind: str, **payload: Any) -> None:
        event = Event(kind=kind, payload=payload)
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Drop on slow consumer — never block producers.
                pass


_bus: EventBus | None = None


def get_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus
