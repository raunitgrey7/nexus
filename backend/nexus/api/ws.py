"""WebSocket live stream (see docs/API.md → WebSocket)."""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool

from nexus.core.logging import get_logger

router = APIRouter()
log = get_logger("nexus.ws")


@router.websocket("/ws/live")
async def live(ws: WebSocket) -> None:
    await ws.accept()
    rt = ws.app.state.runtime
    bridge = ws.app.state.bridge
    queue = bridge.subscribe()
    tick_every = 1
    counter = 0
    try:
        world = await run_in_threadpool(rt.world_dict, "open", True)
        await ws.send_text(
            json.dumps(
                {
                    "type": "hello",
                    "world": world,
                    "kpis": world.get("kpis"),
                    "status": rt.status().model_dump(),
                },
                default=str,
            )
        )

        async def reader() -> None:
            nonlocal tick_every
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                kind = msg.get("type")
                if kind == "ping":
                    await ws.send_text(json.dumps({"type": "pong", "tick": rt.engine.world.clock.tick}))
                elif kind == "subscribe":
                    tick_every = max(1, int(msg.get("tick_every", 1)))
                elif kind == "control":
                    await run_in_threadpool(
                        rt.control,
                        msg.get("action", "start"),
                        int(msg.get("ticks", 1)),
                        msg.get("ticks_per_second"),
                        None,
                        None,
                        None,
                        msg.get("autopilot"),
                    )

        reader_task = asyncio.create_task(reader())
        while True:
            frame: dict[str, Any] = await queue.get()
            if frame.get("type") == "tick":
                counter += 1
                if counter % tick_every:
                    continue
            await ws.send_text(json.dumps(frame, default=str))
            if reader_task.done():
                break
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.info("ws.closed", error=str(exc)[:120])
    finally:
        bridge.unsubscribe(queue)
        with contextlib.suppress(Exception):
            reader_task.cancel()  # type: ignore[possibly-undefined]
