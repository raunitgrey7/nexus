"""Core routes: health, status, control, world, KPIs, spatial, events, faults, timeline, benchmarks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from nexus import __version__
from nexus.api.deps import get_runtime
from nexus.api.schemas import (
    EntityRelations,
    EventModel,
    FaultPreset,
    InjectEventRequest,
    KPIModel,
    OkResponse,
    SimControlRequest,
    SimStatus,
    SpatialResponse,
    TimelineResponse,
)
from nexus.events.types import EventType
from nexus.runtime.live import LiveRuntime
from nexus.simulation.metrics import compute_kpis

router = APIRouter(tags=["core"])
RESULTS = Path(__file__).resolve().parents[3] / "benchmarks" / "results"


@router.get("/health")
def health(rt: LiveRuntime = Depends(get_runtime)) -> dict[str, Any]:
    return {
        "status": "ok",
        "version": __version__,
        "tick": rt.engine.world.clock.tick,
        "running": rt.running,
        "llm": rt.llm.status(),
    }


@router.get("/status", response_model=SimStatus)
def status(rt: LiveRuntime = Depends(get_runtime)) -> SimStatus:
    return rt.status()


@router.post("/sim/control", response_model=SimStatus)
async def control(body: SimControlRequest, rt: LiveRuntime = Depends(get_runtime)) -> SimStatus:
    if body.scale is not None and body.scale not in ("tiny", "small", "medium", "large"):
        raise HTTPException(400, "unknown scale")
    if body.strategy is not None and body.strategy not in {s["name"] for s in rt.strategies()}:
        raise HTTPException(400, "unknown strategy")
    if body.action == "step" and body.ticks > 20_000:
        raise HTTPException(400, "step at most 20000 ticks")
    return await run_in_threadpool(
        rt.control,
        body.action,
        body.ticks,
        body.ticks_per_second,
        body.scale,
        body.seed,
        body.strategy,
        body.autopilot,
    )


@router.get("/world")
async def world(
    orders: str = Query("open", pattern="^(open|all|none)$"),
    grid: bool = True,
    rt: LiveRuntime = Depends(get_runtime),
) -> dict[str, Any]:
    return await run_in_threadpool(rt.world_dict, orders, grid)


@router.get("/world/robots")
def robots(rt: LiveRuntime = Depends(get_runtime)) -> list[dict[str, Any]]:
    with rt.lock:
        return [r.to_dict() for r in rt.engine.world.robots.values()]


@router.get("/world/orders")
def orders(
    status: str | None = None, limit: int = Query(200, ge=1, le=5000), rt: LiveRuntime = Depends(get_runtime)
) -> list[dict[str, Any]]:
    with rt.lock:
        items = list(rt.engine.world.orders.values())
        if status:
            items = [o for o in items if o.status.value == status]
        items.sort(key=lambda o: -o.created_tick)
        return [o.to_dict() for o in items[:limit]]


@router.get("/world/entity/{entity_id}")
def entity(entity_id: str, rt: LiveRuntime = Depends(get_runtime)) -> dict[str, Any]:
    info = rt.entity(entity_id)
    if info is None:
        raise HTTPException(404, f"entity {entity_id} not found")
    info["relations"] = rt.relations(entity_id)
    return info


@router.get("/world/relations/{entity_id}", response_model=EntityRelations)
def relations(entity_id: str, rt: LiveRuntime = Depends(get_runtime)) -> EntityRelations:
    return EntityRelations(**rt.relations(entity_id))


@router.get("/kpis", response_model=KPIModel)
def kpis(since_tick: int = Query(0, ge=0), rt: LiveRuntime = Depends(get_runtime)) -> KPIModel:
    return KPIModel(**rt.kpis(since_tick))


@router.get("/spatial", response_model=SpatialResponse)
async def spatial(rt: LiveRuntime = Depends(get_runtime)) -> SpatialResponse:
    return SpatialResponse(**await run_in_threadpool(rt.spatial))


@router.get("/events", response_model=list[EventModel])
def events(
    since_seq: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=5000),
    types: str | None = None,
    rt: LiveRuntime = Depends(get_runtime),
) -> list[EventModel]:
    wanted = [t.strip() for t in types.split(",") if t.strip()] if types else None
    for t in wanted or []:
        if t not in EventType.__members__:
            raise HTTPException(400, f"unknown event type {t}")
    return [EventModel(**e) for e in rt.events_since(since_seq, limit, wanted)]


@router.get("/events/recent", response_model=list[EventModel])
def recent(
    limit: int = Query(100, ge=1, le=2000), notable: bool = True, rt: LiveRuntime = Depends(get_runtime)
) -> list[EventModel]:
    return [EventModel(**e) for e in rt.recent_events(limit, notable)]


@router.post("/events/inject", response_model=EventModel)
def inject(body: InjectEventRequest, rt: LiveRuntime = Depends(get_runtime)) -> EventModel:
    if body.type not in EventType.__members__:
        raise HTTPException(400, f"unknown event type {body.type}")
    ev = rt.inject(body.type, body.entity_id, body.payload, key=body.key)
    if ev is None:
        raise HTTPException(409, "duplicate event (idempotency key already applied)")
    return EventModel(**ev)


@router.get("/faults/presets", response_model=list[FaultPreset])
def fault_presets(rt: LiveRuntime = Depends(get_runtime)) -> list[FaultPreset]:
    return rt.fault_presets()


@router.post("/faults/{preset_id}", response_model=EventModel)
def fire_preset(preset_id: str, rt: LiveRuntime = Depends(get_runtime)) -> EventModel:
    try:
        ev = rt.fire_preset(preset_id)
    except KeyError as exc:
        raise HTTPException(404, f"unknown preset {preset_id}") from exc
    if ev is None:
        raise HTTPException(409, "preset already applied or nothing to do")
    return EventModel(**ev)


@router.get("/timeline", response_model=TimelineResponse)
def timeline(
    from_tick: int = Query(0, ge=0), to_tick: int | None = None, rt: LiveRuntime = Depends(get_runtime)
) -> TimelineResponse:
    return TimelineResponse(**rt.timeline(from_tick, to_tick))


@router.get("/snapshots/{tick}")
async def snapshot(tick: int, rt: LiveRuntime = Depends(get_runtime)) -> dict[str, Any]:
    world = await run_in_threadpool(rt.snapshot_world, tick)
    if world is None:
        raise HTTPException(404, f"no snapshot at tick {tick}; see /api/timeline")
    payload = world.to_dict(orders="open", include_grid=True)
    payload["kpis"] = compute_kpis(world).to_dict()
    return payload


@router.get("/strategies")
def strategies(rt: LiveRuntime = Depends(get_runtime)) -> list[dict[str, str]]:
    return rt.strategies()


@router.get("/benchmarks")
def benchmarks() -> dict[str, Any]:
    latest = RESULTS / "latest.json"
    if not latest.exists():
        raise HTTPException(404, "no benchmark results yet — run `make bench`")
    return json.loads(latest.read_text(encoding="utf-8"))


@router.post("/persistence/flush", response_model=OkResponse, include_in_schema=False)
async def flush(rt: LiveRuntime = Depends(get_runtime)) -> OkResponse:
    return OkResponse(message="ok")
