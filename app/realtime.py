"""
Event-driven push hub — no polling.

Each WebSocket subscriber registers a queue for a key. Data is pushed
to that queue only when a write actually happens (onboarding complete,
score submitted, profile updated) — not on a timer. This eliminates all
background Supabase queries; the database is only hit when the app
explicitly changes something.

On WS connect the route handler fetches the current snapshot once and
sends it immediately, so the client always gets an up-to-date value on
reconnect without any polling.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Set

logger = logging.getLogger("crescendo.realtime")


class EventHub:
    """Push-only fan-out hub. No background tasks, no polling."""

    def __init__(self) -> None:
        self._subscribers: Dict[str, Set[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, key: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        async with self._lock:
            self._subscribers.setdefault(key, set()).add(queue)
        logger.debug("EventHub: subscriber added for %s", key)
        return queue

    async def unsubscribe(self, key: str, queue: asyncio.Queue) -> None:
        async with self._lock:
            subs = self._subscribers.get(key)
            if subs:
                subs.discard(queue)
                if not subs:
                    self._subscribers.pop(key, None)
        logger.debug("EventHub: subscriber removed for %s", key)

    async def push(self, key: str, value: Any) -> None:
        """Push a value to all subscribers of key. Called after writes."""
        async with self._lock:
            queues = list(self._subscribers.get(key, ()))
        if not queues:
            return
        logger.info("EventHub: pushing update to %d subscriber(s) for %s", len(queues), key)
        for q in queues:
            if q.full():
                try:
                    q.get_nowait()  # drop stale value, keep only latest
                except asyncio.QueueEmpty:
                    pass
            await q.put(value)

    @property
    def active_keys(self) -> list[str]:
        return list(self._subscribers.keys())


# Keep PollerHub name as an alias so existing imports don't break
PollerHub = EventHub
