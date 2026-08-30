"""Optional Redis publisher for live tick frames (fan-out to other processes / services)."""

from __future__ import annotations

import json
from typing import Any

from nexus.core.logging import get_logger

log = get_logger("nexus.redis")


class RedisPublisher:
    def __init__(self, url: str | None, channel: str = "nexus:live") -> None:
        self.url = url
        self.channel = channel
        self._client: Any | None = None
        self.published = 0

    async def connect(self) -> bool:
        if not self.url:
            return False
        try:
            import redis.asyncio as redis

            self._client = redis.from_url(self.url)
            await self._client.ping()
            log.info("redis.connected", url=self.url)
            return True
        except Exception as exc:
            log.warning("redis.unavailable", error=str(exc)[:120])
            self._client = None
            return False

    async def publish(self, frame: dict[str, Any]) -> None:
        if self._client is None:
            return
        try:
            await self._client.publish(self.channel, json.dumps(frame, default=str))
            self.published += 1
        except Exception as exc:
            log.debug("redis.publish_failed", error=str(exc)[:120])

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
