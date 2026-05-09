"""Per-turn in-process pub/sub broker for detached chat sessions.

The background task that generates an assistant turn owns a
:class:`TurnBroker`; every connected WebSocket viewer of that session
subscribes to receive token / citations / tool / done / error events.
If all viewers disconnect the task keeps running — the broker simply
has no listeners for a while. When a viewer reconnects it replays the
accumulator from SQLite first, then subscribes for subsequent events.

No cross-process pub/sub. Chat lives inside one uvicorn worker today;
scaling out is out of scope.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BrokerEvent:
    kind: str  # "token" | "citations" | "tool" | "done" | "error" | "resume"
    data: Any = None


@dataclass
class TurnBroker:
    """Fan-out bus for one running chat turn.

    Each subscriber owns an ``asyncio.Queue`` and consumes events until
    a terminal event (``done`` / ``error``) is published, after which
    the broker is marked ``closed`` and ``get_broker`` refuses to return it.
    """

    turn_id: str
    _queues: list[asyncio.Queue[BrokerEvent]] = field(default_factory=list)
    _closed: bool = False

    def subscribe(self) -> asyncio.Queue[BrokerEvent]:
        q: asyncio.Queue[BrokerEvent] = asyncio.Queue(maxsize=0)
        self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[BrokerEvent]) -> None:
        try:
            self._queues.remove(q)
        except ValueError:
            pass

    def publish(self, event: BrokerEvent) -> None:
        for q in list(self._queues):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:  # pragma: no cover
                pass
        if event.kind in ("done", "error"):
            self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed


# Module-level registry of active brokers. Keyed by turn_id. We also
# index by session_id so a reconnecting viewer can find "the running
# turn for this session" without knowing its id.
_brokers_by_turn: dict[str, TurnBroker] = {}
_turn_by_session: dict[str, str] = {}


def create_broker(turn_id: str, session_id: str) -> TurnBroker:
    broker = TurnBroker(turn_id=turn_id)
    _brokers_by_turn[turn_id] = broker
    _turn_by_session[session_id] = turn_id
    return broker


def get_broker(turn_id: str) -> TurnBroker | None:
    broker = _brokers_by_turn.get(turn_id)
    if broker is None or broker.closed:
        return None
    return broker


def broker_for_session(session_id: str) -> TurnBroker | None:
    turn_id = _turn_by_session.get(session_id)
    if turn_id is None:
        return None
    return get_broker(turn_id)


def discard_broker(turn_id: str, session_id: str) -> None:
    _brokers_by_turn.pop(turn_id, None)
    if _turn_by_session.get(session_id) == turn_id:
        _turn_by_session.pop(session_id, None)
