"""In-process pub/sub that fans events out to WebSocket clients.

Channels:
  "operators"         — every logged-in operator/admin panel
  "customer:<id>"     — one customer (widget WS, Telegram relay); data carries ticket ids
"""

import asyncio
import logging
from collections import defaultdict
from typing import Any

log = logging.getLogger(__name__)


class EventHub:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)
        # Extra listeners inside the process (e.g. the Telegram bot relaying operator replies).
        self._callbacks: list = []

    def subscribe(self, channel: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subs[channel].add(q)
        return q

    def unsubscribe(self, channel: str, q: asyncio.Queue) -> None:
        self._subs[channel].discard(q)

    def add_listener(self, cb) -> None:  # cb(channel, event) -> Awaitable | None
        self._callbacks.append(cb)

    async def publish(self, channel: str, event_type: str, data: dict[str, Any]) -> None:
        event = {"type": event_type, "data": data}
        for q in list(self._subs.get(channel, ())):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                log.warning("dropping event for slow subscriber on %s", channel)
        for cb in self._callbacks:
            try:
                res = cb(channel, event)
                if asyncio.iscoroutine(res):
                    await res
            except Exception:  # a broken listener must not break the publisher
                log.exception("event listener failed")


hub = EventHub()
