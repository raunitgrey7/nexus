"""Synchronous in-process event bus with an asyncio bridge.

The engine is synchronous and deterministic; subscribers must not mutate the world. The
:class:`AsyncBridge` forwards events into asyncio queues (WebSocket clients, background persistence)
from whichever thread the engine runs in.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict
from collections.abc import Callable, Iterable
from typing import Any

from nexus.events.types import Event, EventType

Handler = Callable[[Event], None]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[EventType | None, list[Handler]] = defaultdict(list)
        self._count = 0

    def subscribe(self, handler: Handler, types: Iterable[EventType] | None = None) -> Callable[[], None]:
        keys: list[EventType | None] = list(types) if types else [None]
        for k in keys:
            self._handlers[k].append(handler)

        def unsubscribe() -> None:
            for k in keys:
                with contextlib.suppress(ValueError):
                    self._handlers[k].remove(handler)

        return unsubscribe

    def publish(self, event: Event) -> None:
        self._count += 1
        handlers = self._handlers.get(event.type)
        if handlers:
            for h in list(handlers):
                h(event)
        wildcard = self._handlers.get(None)
        if wildcard:
            for h in list(wildcard):
                h(event)

    @property
    def has_subscribers(self) -> bool:
        return any(self._handlers.values())

    @property
    def published(self) -> int:
        return self._count


class AsyncBridge:
    """Fan events out to asyncio consumers. Safe to call from a worker thread."""

    def __init__(self, loop: asyncio.AbstractEventLoop | None = None, max_queue: int = 2_000) -> None:
        self.loop = loop
        self.max_queue = max_queue
        self.queues: set[asyncio.Queue[Any]] = set()

    def attach(self, bus: EventBus, types: Iterable[EventType] | None = None) -> Callable[[], None]:
        return bus.subscribe(self.push, types)

    def subscribe(self) -> asyncio.Queue[Any]:
        q: asyncio.Queue[Any] = asyncio.Queue(maxsize=self.max_queue)
        self.queues.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[Any]) -> None:
        self.queues.discard(q)

    def push(self, item: Any) -> None:
        if not self.queues:
            return
        loop = self.loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._deliver, item)

    def _deliver(self, item: Any) -> None:
        for q in list(self.queues):
            if q.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()  # drop oldest — live streams prefer freshness over completeness
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(item)
