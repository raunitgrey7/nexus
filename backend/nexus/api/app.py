"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from nexus import __version__
from nexus.api.routes.core import router as core_router
from nexus.api.routes.intelligence import router as intel_router
from nexus.api.ws import router as ws_router
from nexus.core.config import settings
from nexus.core.logging import configure_logging, get_logger
from nexus.events.bus import AsyncBridge
from nexus.observability import metrics, setup_tracing
from nexus.persistence import RedisPublisher, make_persistence
from nexus.runtime.live import LiveRuntime

log = get_logger("nexus.api")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    loop = asyncio.get_running_loop()
    runtime: LiveRuntime = (
        app.state.runtime
        if getattr(app.state, "runtime", None)
        else LiveRuntime(strategy=getattr(app.state, "strategy", "optimized"))
    )
    app.state.runtime = runtime
    bridge = AsyncBridge(loop)
    runtime.frame_listeners.append(bridge.push)
    app.state.bridge = bridge
    persistence = make_persistence(settings.database_url, runtime.run_id)
    app.state.persistence = persistence
    try:
        await persistence.connect()
        runtime.event_sinks.append(persistence.event_sink)

        def _save_decision(d: Any) -> None:
            asyncio.run_coroutine_threadsafe(persistence.save_decision(d.model_dump()), loop)

        def _save_whatif(r: Any) -> None:
            asyncio.run_coroutine_threadsafe(persistence.save_whatif(r.model_dump()), loop)

        runtime.ops.listeners.append(_save_decision)
        runtime.whatif.on_done = _chain(runtime.whatif.on_done, _save_whatif)
    except Exception as exc:
        log.warning("db.disabled", error=str(exc)[:200])
        app.state.persistence = make_persistence(None, runtime.run_id)
    redis_pub = RedisPublisher(settings.redis_url)
    app.state.redis = redis_pub
    if await redis_pub.connect():

        def _publish(frame: dict[str, Any]) -> None:
            if frame.get("type") in ("tick", "event", "decision"):
                asyncio.run_coroutine_threadsafe(redis_pub.publish(frame), loop)

        runtime.frame_listeners.append(_publish)
    metrics.TICK_RATE.set(runtime.ticks_per_second)
    if getattr(app.state, "autostart", True):
        runtime.start()
    log.info(
        "api.started",
        version=__version__,
        scale=runtime.scale,
        strategy=runtime.strategy_name,
        llm=runtime.llm.status(),
    )
    try:
        yield
    finally:
        runtime.close()
        await app.state.persistence.close()
        await redis_pub.close()
        log.info("api.stopped")


def _chain(first: Any, second: Any) -> Any:
    def run(x: Any) -> None:
        if first is not None:
            first(x)
        second(x)

    return run


def create_app(
    runtime: LiveRuntime | None = None, autostart: bool = True, strategy: str = "optimized"
) -> FastAPI:
    app = FastAPI(
        title="NEXUS — Autonomous Operations Intelligence",
        version=__version__,
        description=(Path(__file__).resolve().parents[3] / "docs" / "API.md").read_text(encoding="utf-8")[
            :4000
        ]
        if (Path(__file__).resolve().parents[3] / "docs" / "API.md").exists()
        else "NEXUS digital twin API",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )
    app.state.runtime = runtime
    app.state.autostart = autostart
    app.state.strategy = strategy
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins or ["*"],
        allow_origin_regex=settings.cors_origin_regex,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(core_router, prefix="/api")
    app.include_router(intel_router, prefix="/api")
    app.include_router(ws_router)

    @app.get("/metrics", include_in_schema=False)
    def prometheus() -> Response:
        return Response(metrics.render(), media_type="text/plain; version=0.0.4; charset=utf-8")

    @app.get("/", include_in_schema=False)
    def root() -> dict[str, Any]:
        return {
            "name": "NEXUS",
            "version": __version__,
            "docs": "/docs",
            "ws": "/ws/live",
            "health": "/api/health",
        }

    setup_tracing(app)
    return app


def dumps(obj: Any) -> str:
    return json.dumps(obj, default=str)
