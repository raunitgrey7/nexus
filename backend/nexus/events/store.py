"""Append-only, idempotent event store.

* Persisted (non-ephemeral) events are kept in order in ``self.log``.
* A bounded ring buffer keeps the most recent events of *all* kinds for the live UI feed.
* ``key`` deduplication makes external commands idempotent: delivering the same command twice
  (retry, duplicate WebSocket frame, agent re-run) applies it once.
* Sinks (Postgres writer, WebSocket bridge, metrics) receive every appended event synchronously;
  sinks must be cheap — heavy work belongs in a queue on the sink side.
"""

from __future__ import annotations

import json
from collections import Counter, deque
from collections.abc import Callable, Iterable, Iterator
from typing import Any

from nexus.events.types import Event, EventType

Sink = Callable[[Event], None]


class DuplicateEventError(ValueError):
    pass


class EventStore:
    def __init__(self, ring_size: int = 5_000, keep_ephemeral: bool = False) -> None:
        self.log: list[Event] = []
        self.recent: deque[Event] = deque(maxlen=ring_size)
        self.keys: set[str] = set()
        self.counts: Counter[str] = Counter()
        self.seq = 0
        self.keep_ephemeral = keep_ephemeral
        self.sinks: list[Sink] = []

    # ---- writing -------------------------------------------------------------------------------
    def append(self, event: Event) -> Event:
        if event.key is not None:
            if event.key in self.keys:
                raise DuplicateEventError(event.key)
            self.keys.add(event.key)
        self.seq += 1
        event.seq = self.seq
        event.id = f"EVT-{self.seq:08d}"
        if not event.ephemeral or self.keep_ephemeral:
            self.log.append(event)
        self.recent.append(event)
        self.counts[event.type.value] += 1
        for sink in self.sinks:
            sink(event)
        return event

    def has_key(self, key: str) -> bool:
        return key in self.keys

    def add_sink(self, sink: Sink) -> None:
        self.sinks.append(sink)

    def remove_sink(self, sink: Sink) -> None:
        if sink in self.sinks:
            self.sinks.remove(sink)

    # ---- reading -------------------------------------------------------------------------------
    def __len__(self) -> int:
        return len(self.log)

    def since(self, seq: int, limit: int = 1000, types: Iterable[EventType] | None = None) -> list[Event]:
        wanted = set(types) if types else None
        out = []
        for ev in self.log:
            if ev.seq <= seq:
                continue
            if wanted and ev.type not in wanted:
                continue
            out.append(ev)
            if len(out) >= limit:
                break
        return out

    def recent_events(self, limit: int = 100, types: Iterable[EventType] | None = None) -> list[Event]:
        wanted = set(types) if types else None
        out: list[Event] = []
        for ev in reversed(self.recent):
            if wanted and ev.type not in wanted:
                continue
            out.append(ev)
            if len(out) >= limit:
                break
        out.reverse()
        return out

    def external_events(self) -> list[Event]:
        return [e for e in self.log if e.external]

    def by_type(self, type_: EventType) -> Iterator[Event]:
        return (e for e in self.log if e.type == type_)

    def between(self, tick_from: int, tick_to: int) -> list[Event]:
        return [e for e in self.log if tick_from <= e.tick < tick_to]

    def stats(self) -> dict[str, Any]:
        return {"persisted": len(self.log), "seq": self.seq, "counts": dict(self.counts)}

    # ---- (de)serialization ---------------------------------------------------------------------
    def dump_jsonl(self) -> str:
        return "\n".join(json.dumps(e.to_dict(), separators=(",", ":")) for e in self.log)

    @staticmethod
    def load_jsonl(text: str) -> list[Event]:
        return [Event.from_dict(json.loads(line)) for line in text.splitlines() if line.strip()]

    def fork(self) -> EventStore:
        """A fresh store for a simulation world (sinks are intentionally not inherited)."""
        clone = EventStore(ring_size=self.recent.maxlen or 5_000, keep_ephemeral=self.keep_ephemeral)
        clone.seq = self.seq
        return clone
