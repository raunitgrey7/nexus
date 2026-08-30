"""Durable persistence (PostgreSQL via SQLAlchemy async; SQLite for tests).

Persisted: non-ephemeral events (append-only), periodic world snapshots (pickled, with digest and
KPIs), decisions and what-if results (JSON). When ``NEXUS_DATABASE_URL`` is unset the runtime uses
:class:`NullPersistence` and everything stays in memory — the platform is fully functional without a
database; the database adds durability and history across restarts.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    select,
    text,
)
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from nexus.core.logging import get_logger
from nexus.events.types import Event

log = get_logger("nexus.db")
metadata = MetaData()

events_table = Table(
    "events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String(64), index=True, nullable=False),
    Column("seq", BigInteger, nullable=False),
    Column("event_id", String(32), nullable=False),
    Column("type", String(48), index=True, nullable=False),
    Column("tick", BigInteger, index=True, nullable=False),
    Column("entity_id", String(64), nullable=True),
    Column("payload", JSON, nullable=False),
    Column("origin", String(24), nullable=False),
    Column("key", String(160), nullable=True),
    Column("cause", String(64), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
snapshots_table = Table(
    "snapshots",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String(64), index=True, nullable=False),
    Column("tick", BigInteger, index=True, nullable=False),
    Column("digest", String(64), nullable=False),
    Column("kpis", JSON, nullable=False),
    Column("data", LargeBinary, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
decisions_table = Table(
    "decisions",
    metadata,
    Column("id", String(48), primary_key=True),
    Column("run_id", String(64), index=True, nullable=False),
    Column("tick", BigInteger, nullable=False),
    Column("status", String(16), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("explanation", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
whatif_table = Table(
    "whatif_results",
    metadata,
    Column("id", String(48), primary_key=True),
    Column("run_id", String(64), index=True, nullable=False),
    Column("tick", BigInteger, nullable=False),
    Column("payload", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)


def _now() -> datetime:
    return datetime.now(UTC)


class NullPersistence:
    enabled = False

    async def connect(self) -> None:
        return

    async def close(self) -> None:
        return

    def event_sink(self, event: Event) -> None:
        return

    async def flush(self) -> int:
        return 0

    async def save_snapshot(self, tick: int, digest: str, kpis: dict[str, Any], data: bytes) -> None:
        return

    async def save_decision(self, decision: dict[str, Any]) -> None:
        return

    async def save_whatif(self, result: dict[str, Any]) -> None:
        return

    async def counts(self) -> dict[str, int]:
        return {}

    async def recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        return []


class Persistence(NullPersistence):
    enabled = True

    def __init__(self, url: str, run_id: str, batch_size: int = 500, flush_interval_s: float = 1.0) -> None:
        self.url = url
        self.run_id = run_id
        self.batch_size = batch_size
        self.flush_interval_s = flush_interval_s
        self.engine: AsyncEngine | None = None
        self._queue: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self.written = 0

    async def connect(self) -> None:
        self.engine = create_async_engine(self.url, pool_pre_ping=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
            await conn.execute(text("SELECT 1"))
        self._task = asyncio.create_task(self._flusher(), name="nexus-db-flusher")
        log.info("db.connected", url=self.url.split("@")[-1], run_id=self.run_id)

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self.flush()
        if self.engine is not None:
            await self.engine.dispose()

    # ---- events (sync sink → async batch writer) ----------------------------------------------
    def event_sink(self, event: Event) -> None:
        if event.ephemeral:
            return
        self._queue.append(
            {
                "run_id": self.run_id,
                "seq": event.seq,
                "event_id": event.id,
                "type": event.type.value,
                "tick": event.tick,
                "entity_id": event.entity_id,
                "payload": json.loads(json.dumps(event.payload, default=str)),
                "origin": event.origin,
                "key": event.key,
                "cause": event.cause,
                "created_at": _now(),
            }
        )

    async def _flusher(self) -> None:
        while True:
            await asyncio.sleep(self.flush_interval_s)
            try:
                await self.flush()
            except Exception as exc:
                log.warning("db.flush_failed", error=str(exc)[:200])

    async def flush(self) -> int:
        if self.engine is None or not self._queue:
            return 0
        async with self._lock:
            batch, self._queue = self._queue[: self.batch_size * 4], self._queue[self.batch_size * 4 :]
        async with self.engine.begin() as conn:
            for i in range(0, len(batch), self.batch_size):
                await conn.execute(events_table.insert(), batch[i : i + self.batch_size])
        self.written += len(batch)
        return len(batch)

    # ---- snapshots / decisions / what-if -------------------------------------------------------
    async def save_snapshot(self, tick: int, digest: str, kpis: dict[str, Any], data: bytes) -> None:
        if self.engine is None:
            return
        async with self.engine.begin() as conn:
            await conn.execute(
                snapshots_table.insert().values(
                    run_id=self.run_id, tick=tick, digest=digest, kpis=kpis, data=data, created_at=_now()
                )
            )

    async def save_decision(self, decision: dict[str, Any]) -> None:
        if self.engine is None:
            return
        async with self.engine.begin() as conn:
            existing = await conn.execute(
                select(decisions_table.c.id).where(decisions_table.c.id == decision["id"])
            )
            payload = json.loads(json.dumps(decision, default=str))
            if existing.first():
                await conn.execute(
                    decisions_table.update()
                    .where(decisions_table.c.id == decision["id"])
                    .values(
                        status=decision["status"],
                        payload=payload,
                        explanation=decision.get("explanation", ""),
                    )
                )
            else:
                await conn.execute(
                    decisions_table.insert().values(
                        id=decision["id"],
                        run_id=self.run_id,
                        tick=decision["created_tick"],
                        status=decision["status"],
                        payload=payload,
                        explanation=decision.get("explanation", ""),
                        created_at=_now(),
                    )
                )

    async def save_whatif(self, result: dict[str, Any]) -> None:
        if self.engine is None:
            return
        async with self.engine.begin() as conn:
            await conn.execute(
                whatif_table.insert().values(
                    id=result["id"],
                    run_id=self.run_id,
                    tick=result["created_tick"],
                    payload=json.loads(json.dumps(result, default=str)),
                    created_at=_now(),
                )
            )

    async def counts(self) -> dict[str, int]:
        if self.engine is None:
            return {}
        out = {}
        async with self.engine.connect() as conn:
            for name, table in (
                ("events", events_table),
                ("snapshots", snapshots_table),
                ("decisions", decisions_table),
                ("whatif", whatif_table),
            ):
                res = await conn.execute(
                    text(f"SELECT COUNT(*) FROM {table.name} WHERE run_id = :r"), {"r": self.run_id}
                )
                out[name] = int(res.scalar() or 0)
        return out

    async def recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        if self.engine is None:
            return []
        async with self.engine.connect() as conn:
            res = await conn.execute(
                select(events_table)
                .where(events_table.c.run_id == self.run_id)
                .order_by(events_table.c.seq.desc())
                .limit(limit)
            )
            return [dict(r._mapping) for r in res]


def make_persistence(url: str | None, run_id: str) -> NullPersistence:
    return Persistence(url, run_id) if url else NullPersistence()
