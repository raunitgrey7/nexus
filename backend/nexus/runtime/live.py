"""Live runtime: owns the live twin and everything that observes or acts on it.

Threading model: a single loop thread advances the engine; every other access (API handlers,
decision pipeline, what-if jobs) takes ``self.lock`` around engine/world touches and does its heavy
work on forked worlds outside the lock. Decisions run in a background thread so the twin never
pauses while the agents think.
"""

from __future__ import annotations

import contextlib
import pickle
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from nexus.agents.ops_manager import OperationsManager
from nexus.api.schemas import (
    DecisionModel,
    FaultPreset,
    Forecast,
    InjectEventRequest,
    SimStatus,
    SnapshotInfo,
    TimelinePoint,
    WhatIfRequest,
    WhatIfResult,
)
from nexus.core.config import settings
from nexus.core.logging import get_logger
from nexus.events.types import NOTABLE_TYPES, Event, EventType
from nexus.forecasting import Forecaster, HistoryRecorder
from nexus.llm.client import LLMClient
from nexus.observability import metrics
from nexus.simulation.engine import SimulationEngine
from nexus.simulation.faults import FaultInjector
from nexus.simulation.metrics import compute_kpis
from nexus.simulation.strategies import STRATEGIES, make_strategy
from nexus.twin.domain import get_domain
from nexus.twin.spatial import SpatialGraph
from nexus.twin.world import WorldState
from nexus.whatif.engine import WhatIfEngine

log = get_logger("nexus.runtime")
STREAMED_TYPES = NOTABLE_TYPES | {
    EventType.ORDER_CREATED,
    EventType.ORDER_DELIVERED,
    EventType.TASK_CREATED,
    EventType.TASK_COMPLETED,
    EventType.ITEM_PICKED,
    EventType.CHARGING_STARTED,
    EventType.CHARGING_COMPLETED,
    EventType.ROBOT_STATUS_CHANGED,
}

FAULT_PRESETS: list[FaultPreset] = [
    FaultPreset(
        id="fail-r07",
        name="Robot R07 fails",
        description="Motor fault, 45 min recovery — the signature incident.",
        event=InjectEventRequest(
            type="ROBOT_FAILURE", entity_id="R07", payload={"cause": "motor_fault", "recovery_ticks": 2700}
        ),
    ),
    FaultPreset(
        id="fail-r03",
        name="Robot R03 fails",
        description="Lidar fault, 30 min recovery.",
        event=InjectEventRequest(
            type="ROBOT_FAILURE", entity_id="R03", payload={"cause": "lidar_fault", "recovery_ticks": 1800}
        ),
    ),
    FaultPreset(
        id="block-aisle-c",
        name="Aisle blocked in Zone C",
        description="A spill blocks an aisle; robots must detour.",
        event=InjectEventRequest(type="AISLE_BLOCKED", payload={"zone_id": "C", "reason": "spill"}),
    ),
    FaultPreset(
        id="close-dock-d2",
        name="Close loading dock D2",
        description="Deliveries rebalance to the other docks.",
        event=InjectEventRequest(type="DOCK_CLOSED", entity_id="D2", payload={"reason": "truck delay"}),
    ),
    FaultPreset(
        id="close-zone-b",
        name="Close Zone B",
        description="Zone B becomes inaccessible.",
        event=InjectEventRequest(type="ZONE_CLOSED", entity_id="B", payload={"reason": "maintenance"}),
    ),
    FaultPreset(
        id="demand-plus-40",
        name="Demand +40%",
        description="Order arrivals ×1.4 from now on.",
        event=InjectEventRequest(type="DEMAND_CHANGED", payload={"multiplier": 1.4}),
    ),
    FaultPreset(
        id="demand-burst",
        name="Demand burst ×2 (30 min)",
        description="A 30-minute surge.",
        event=InjectEventRequest(
            type="DEMAND_CHANGED", payload={"burst_multiplier": 2.0, "burst_ticks": 1800}
        ),
    ),
    FaultPreset(
        id="charger-off",
        name="Disable charger CH01",
        description="Charging capacity shrinks.",
        event=InjectEventRequest(
            type="CHARGER_DISABLED", entity_id="CH01", payload={"reason": "maintenance"}
        ),
    ),
    FaultPreset(
        id="worker-delay",
        name="Loader W01 delayed 30 min",
        description="Unloading at their dock slows down.",
        event=InjectEventRequest(type="WORKER_DELAY", entity_id="W01", payload={"ticks": 1800}),
    ),
    FaultPreset(
        id="recover-all",
        name="Recover all robots",
        description="Bring failed robots back online.",
        event=InjectEventRequest(type="ROBOT_RECOVERED", entity_id="*", payload={}),
    ),
    FaultPreset(
        id="reset-demand",
        name="Reset demand",
        description="Multiplier back to 1.0, clear bursts.",
        event=InjectEventRequest(
            type="DEMAND_CHANGED", payload={"multiplier": 1.0, "burst_multiplier": 1.0, "burst_until_tick": 0}
        ),
    ),
]


class LiveRuntime:
    def __init__(
        self,
        scale: str | None = None,
        seed: int | None = None,
        strategy: str = "optimized",
        ticks_per_second: float | None = None,
        llm: LLMClient | None = None,
        workers: int | None = None,
        autostart: bool = False,
    ) -> None:
        self.lock = threading.RLock()
        self.llm = llm if llm is not None else LLMClient()
        self.workers = workers
        self.ticks_per_second = ticks_per_second or settings.live_ticks_per_second
        self.running = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.started_at = time.time()
        self.frame_listeners: list[Callable[[dict[str, Any]], None]] = []
        self.event_sinks: list[Callable[[Event], None]] = []
        self.snapshots: deque[tuple[int, str, dict[str, float], bytes]] = deque(
            maxlen=max(2, settings.snapshot_ring)
        )
        self.last_forecast: Forecast | None = None
        self._last_frame_wall = 0.0
        self._decision_thread: threading.Thread | None = None
        self.run_id = f"run-{int(self.started_at)}"
        self._build(
            scale or settings.default_scale, seed if seed is not None else settings.default_seed, strategy
        )
        if autostart:
            self.start()

    # ---- construction --------------------------------------------------------------------------
    def _build(self, scale: str, seed: int, strategy_name: str) -> None:
        with self.lock:
            world = get_domain("warehouse").build(scale, seed=seed)
            strategy = make_strategy(strategy_name)
            self.engine = SimulationEngine(world, strategy, fault_injector=FaultInjector(spontaneous=True))
            self.recorder = HistoryRecorder()
            self.engine.hooks.append(self.recorder.hook)
            self.forecaster = Forecaster()
            self.ops = OperationsManager(
                self.engine, self.llm, self.forecaster, self.recorder, workers=self.workers, lock=self.lock
            )
            self.ops.listeners.append(lambda d: self._push({"type": "decision", "decision": d.model_dump()}))
            self.whatif = WhatIfEngine(
                lambda: self.engine,
                self.workers,
                on_done=lambda r: self._push({"type": "whatif", "result": r.model_dump()}),
            )
            self.engine.bus.subscribe(self._on_event)
            self.engine.store.add_sink(self._on_store_event)
            self.scale, self.seed, self.strategy_name = scale, seed, strategy_name
            self.snapshots.clear()
            self.last_forecast = None
            self._take_snapshot()
            log.info(
                "runtime.built", scale=scale, seed=seed, strategy=strategy_name, robots=len(world.robots)
            )

    # ---- loop ----------------------------------------------------------------------------------
    def start(self) -> None:
        self.running = True
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="nexus-live-loop", daemon=True)
            self._thread.start()
        self._push({"type": "status", "status": self.status().model_dump()})

    def pause(self) -> None:
        self.running = False
        self._push({"type": "status", "status": self.status().model_dump()})

    def close(self) -> None:
        self.running = False
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _loop(self) -> None:
        budget = 0.0
        last = time.perf_counter()
        while not self._stop.is_set():
            if not self.running:
                time.sleep(0.05)
                last = time.perf_counter()
                budget = 0.0
                continue
            now = time.perf_counter()
            budget += (now - last) * self.ticks_per_second
            last = now
            steps = int(budget)
            if steps <= 0:
                time.sleep(min(0.02, 1.0 / max(1.0, self.ticks_per_second)))
                continue
            budget -= steps
            for _ in range(min(steps, 200)):
                self.step()
            if steps > 200:
                budget = 0.0

    def step(self, n: int = 1) -> None:
        for _ in range(n):
            t0 = time.perf_counter()
            with self.lock:
                self.engine.step()
                tick = self.engine.world.clock.tick
                self._after_step(tick)
            metrics.STEP_DURATION.observe(time.perf_counter() - t0)

    def _after_step(self, tick: int) -> None:
        world = self.engine.world
        if settings.snapshot_every_ticks and tick % settings.snapshot_every_ticks == 0:
            self._take_snapshot()
        if tick % 60 == 0:
            k = compute_kpis(world)
            metrics.update_kpis(k.to_dict(), tick, dict(world.zone_occupancy))
            with contextlib.suppress(Exception):
                self.last_forecast = self.forecaster.quick(world, self.recorder)
                if tick % 300 == 0:
                    self.last_forecast = self.forecaster.forecast(
                        world, self.recorder, self.engine.pathfinder
                    )
                self._push({"type": "forecast", "forecast": self.last_forecast.model_dump()})
        now = time.perf_counter()
        if now - self._last_frame_wall >= 0.05 or self.ticks_per_second <= 20:
            self._last_frame_wall = now
            self._push(self.tick_frame())
        trigger = self.ops.poll_trigger()
        if trigger and (self._decision_thread is None or not self._decision_thread.is_alive()):
            self._decision_thread = threading.Thread(
                target=self._autopilot_decide, args=(trigger,), name="nexus-autopilot", daemon=True
            )
            self._decision_thread.start()

    def _autopilot_decide(self, trigger: str) -> None:
        try:
            t0 = time.perf_counter()
            decision = self.ops.decide_and_maybe_execute(f"autopilot:{trigger}")
            metrics.PLANNING_LATENCY.observe(time.perf_counter() - t0)
            metrics.DECISIONS.labels(status=decision.status).inc()
        except Exception:
            log.exception("runtime.autopilot_failed")

    # ---- events --------------------------------------------------------------------------------
    def _on_event(self, event: Event) -> None:
        self.ops.on_event(event)
        if event.type in STREAMED_TYPES:
            self._push({"type": "event", "event": event.to_dict()})

    def _on_store_event(self, event: Event) -> None:
        if not event.ephemeral:
            metrics.EVENTS.labels(type=event.type.value).inc()
        for sink in self.event_sinks:
            sink(event)

    def _push(self, frame: dict[str, Any]) -> None:
        for fn in list(self.frame_listeners):
            try:
                fn(frame)
            except Exception as exc:
                log.debug("runtime.push_failed", error=str(exc)[:80])

    # ---- snapshots -----------------------------------------------------------------------------
    def _take_snapshot(self) -> None:
        world = self.engine.world
        k = compute_kpis(world).headline()
        data = world.snapshot_bytes()
        self.snapshots.append((world.clock.tick, world.digest(), k, data))
        self.engine.emit(
            EventType.SNAPSHOT_TAKEN, None, {"digest": self.snapshots[-1][1], "bytes": len(data)}
        )

    def snapshot_infos(self) -> list[SnapshotInfo]:
        return [
            SnapshotInfo(
                tick=t,
                sim_time=self.engine.world.clock.epoch.isoformat(),
                digest=d,
                kpis=k,
                size_bytes=len(b),
            )
            for t, d, k, b in self.snapshots
        ]

    def snapshot_world(self, tick: int) -> WorldState | None:
        for t, _, _, data in self.snapshots:
            if t == tick:
                return WorldState.from_snapshot(data)
        return None

    # ---- control -------------------------------------------------------------------------------
    def control(
        self,
        action: str,
        ticks: int = 1,
        ticks_per_second: float | None = None,
        scale: str | None = None,
        seed: int | None = None,
        strategy: str | None = None,
        autopilot: bool | None = None,
    ) -> SimStatus:
        if ticks_per_second is not None:
            self.ticks_per_second = float(ticks_per_second)
            metrics.TICK_RATE.set(self.ticks_per_second)
        if autopilot is not None:
            self.ops.autopilot = bool(autopilot)
        if action == "start":
            self.start()
        elif action == "pause":
            self.pause()
        elif action == "step":
            self.running = False
            self.step(min(ticks, 20_000))
        elif action == "reset":
            was_running = self.running
            self.running = False
            time.sleep(0.05)
            self._build(
                scale or self.scale, seed if seed is not None else self.seed, strategy or self.strategy_name
            )
            if was_running:
                self.start()
        elif action == "speed":
            pass
        status = self.status()
        self._push({"type": "status", "status": status.model_dump()})
        return status

    def set_strategy(self, name: str) -> None:
        with self.lock:
            self.engine.strategy = make_strategy(name)
            self.strategy_name = name

    def status(self) -> SimStatus:
        with self.lock:
            world = self.engine.world
            return SimStatus(
                running=self.running,
                tick=world.clock.tick,
                sim_time=world.clock.now().isoformat(),
                ticks_per_second=self.ticks_per_second,
                strategy=getattr(self.engine.strategy, "name", self.strategy_name),
                scale=self.scale,
                seed=self.seed,
                domain=world.domain,
                autopilot=self.ops.autopilot,
                events_persisted=len(self.engine.store),
                decisions=len(self.ops.order),
                llm={
                    "enabled": self.llm.enabled,
                    "model": self.llm.model,
                    "available": self.llm.available(),
                    "url": self.llm.url,
                },  # type: ignore[arg-type]
                uptime_s=round(time.time() - self.started_at, 1),
            )

    # ---- reads ---------------------------------------------------------------------------------
    def world_dict(self, orders: str = "open", include_grid: bool = True) -> dict[str, Any]:
        with self.lock:
            payload = self.engine.world.to_dict(orders=orders, include_grid=include_grid)
            payload["kpis"] = compute_kpis(self.engine.world).to_dict()
            payload["strategy"] = self.engine.strategy.describe()
            return payload

    def tick_frame(self) -> dict[str, Any]:
        world = self.engine.world
        k = self.recorder.latest() if len(self.recorder) else None
        kp = compute_kpis(world) if (k is None or world.clock.tick % 60 == 0) else None
        headline = (
            kp.headline()
            | {
                "orders_open": kp.orders_open,
                "orders_pending": kp.orders_pending,
                "robots_operational": kp.robots_operational,
            }
            if kp is not None
            else self._cached_headline
        )
        self._cached_headline = headline
        return {
            "type": "tick",
            "tick": world.clock.tick,
            "sim_time": world.clock.now().isoformat(),
            "robots": [
                {
                    "id": r.id,
                    "cell": list(r.cell),
                    "status": r.status.value,
                    "battery": round(r.battery, 1),
                    "task_id": r.task_id,
                    "path": [list(c) for c in r.path[:24]],
                    "zone_id": r.zone_id,
                    "load": r.load,
                }
                for r in world.robots.values()
            ],
            "kpis": headline,
            "zone_occupancy": dict(world.zone_occupancy),
            "docks": [
                {"id": d.id, "queue": len(d.queue), "open": d.open, "delivered": d.delivered}
                for d in world.docks.values()
            ],
            "chargers": [
                {"id": c.id, "occupants": list(c.occupants), "enabled": c.enabled}
                for c in world.chargers.values()
            ],
            "blocked": [list(world.grid.cell_of(i)) for i in sorted(world.grid.blocked)][:200],
            "closed_zones": sorted(world.grid.closed_zones),
            "congestion": world.congestion_total(),
        }

    _cached_headline: dict[str, Any] = {}

    def kpis(self, since_tick: int = 0) -> dict[str, Any]:
        with self.lock:
            return compute_kpis(self.engine.world, since_tick).to_dict()

    def entity(self, entity_id: str) -> dict[str, Any] | None:
        with self.lock:
            w = self.engine.world
            for kind, coll in (
                ("robot", w.robots),
                ("order", w.orders),
                ("zone", w.zones),
                ("shelf", w.shelves),
                ("dock", w.docks),
                ("charger", w.chargers),
                ("worker", w.workers),
                ("task", w.tasks),
            ):
                obj = coll.get(entity_id)  # type: ignore[attr-defined]
                if obj is not None:
                    info: dict[str, Any] = {"kind": kind, "entity": obj.to_dict()}
                    if kind == "zone":
                        info["robots"] = w.zone_occupancy.get(entity_id, 0)
                        info["open_orders"] = sum(
                            1
                            for o in w.open_orders()
                            if any(
                                w.shelves[ln.shelf_id].zone_id == entity_id
                                for ln in o.lines
                                if ln.shelf_id in w.shelves
                            )
                        )
                        info["shelves"] = len(w.shelves_in_zone(entity_id))
                    return info
            return None

    def spatial(self) -> dict[str, Any]:
        with self.lock:
            sg = SpatialGraph(self.engine.world)
            data = sg.to_dict()
            data["zone_load"] = sg.zone_load()
            data["zone_adjacency"] = {z: sorted(n) for z, n in self.engine.world.zone_adjacency().items()}
            return data

    def relations(self, entity_id: str) -> dict[str, Any]:
        with self.lock:
            sg = SpatialGraph(self.engine.world)
            return {
                "entity_id": entity_id,
                "kind": sg.graph.nodes[entity_id].get("kind") if entity_id in sg.graph else None,
                "triples": [list(t) for t in sg.relations_of(entity_id)],
                "description": sg.describe(entity_id),
            }

    def events_since(self, seq: int, limit: int, types: list[str] | None) -> list[dict[str, Any]]:
        with self.lock:
            wanted = [EventType(t) for t in types] if types else None
            return [e.to_dict() for e in self.engine.store.since(seq, limit, wanted)]

    def recent_events(self, limit: int, notable: bool) -> list[dict[str, Any]]:
        with self.lock:
            return [
                e.to_dict()
                for e in self.engine.store.recent_events(limit, NOTABLE_TYPES if notable else None)
            ]

    # ---- writes --------------------------------------------------------------------------------
    def inject(
        self,
        type_: str,
        entity_id: str | None,
        payload: dict[str, Any],
        key: str | None = None,
        origin: str = "user",
    ) -> dict[str, Any] | None:
        etype = EventType(type_)
        with self.lock:
            world = self.engine.world
            if (
                etype == EventType.AISLE_BLOCKED
                and "cells" not in payload
                and payload.get("zone_id") in world.zones
            ):
                z = world.zones[payload["zone_id"]]
                payload = {**payload, "cells": [[z.x0 + 3, y] for y in range(z.y0 + 1, z.y1)]}
            if etype == EventType.ROBOT_RECOVERED and entity_id == "*":
                last = None
                for r in world.failed_robots():
                    last = self.engine.inject(etype, r.id, {}, origin=origin)
                return last.to_dict() if last else None
            ev = self.engine.inject(etype, entity_id, payload, origin=origin, key=key)
            return ev.to_dict() if ev else None

    def fault_presets(self) -> list[FaultPreset]:
        with self.lock:
            robots = set(self.engine.world.robots)
        return [
            p
            for p in FAULT_PRESETS
            if p.event.entity_id is None
            or p.event.entity_id in robots
            or not p.event.entity_id.startswith("R")
        ]

    def fire_preset(self, preset_id: str) -> dict[str, Any] | None:
        preset = next((p for p in FAULT_PRESETS if p.id == preset_id), None)
        if preset is None:
            raise KeyError(preset_id)
        return self.inject(
            preset.event.type,
            preset.event.entity_id,
            dict(preset.event.payload),
            key=f"preset:{preset_id}:{self.engine.world.clock.tick}",
            origin="user",
        )

    # ---- intelligence --------------------------------------------------------------------------
    def forecast(self, horizon_min: int = 90) -> Forecast:
        with self.lock:
            world = self.engine.world.fork("forecast")
            history = self.recorder.fork()
            pathfinder = self.engine.pathfinder
        fc = self.forecaster.forecast(world, history, pathfinder, horizon_min=horizon_min)
        self.last_forecast = fc
        return fc

    def decide(
        self,
        goal: str,
        trigger: str = "manual",
        horizon_min: int = 90,
        candidates: int | None = None,
        use_llm: bool | None = None,
        context: dict[str, Any] | None = None,
    ) -> DecisionModel:
        t0 = time.perf_counter()
        horizon_ticks = int(horizon_min * 60 / self.engine.world.clock.tick_seconds)
        decision = self.ops.decide(
            goal=goal,
            trigger=trigger,
            horizon_ticks=horizon_ticks,
            n_candidates=candidates,
            use_llm=use_llm,
            context=context,
        )
        metrics.PLANNING_LATENCY.observe(time.perf_counter() - t0)
        metrics.DECISIONS.labels(status=decision.status).inc()
        metrics.SIMULATIONS.labels(kind="decision").inc(len(decision.candidates))
        return decision

    def whatif_run(self, request: WhatIfRequest) -> WhatIfResult:
        t0 = time.perf_counter()
        result = self.whatif.run(request)
        metrics.WHATIF_LATENCY.observe(time.perf_counter() - t0)
        metrics.SIMULATIONS.labels(kind="whatif").inc(len(result.runs) + (1 if result.reference else 0))
        return result

    def timeline(self, from_tick: int = 0, to_tick: int | None = None) -> dict[str, Any]:
        with self.lock:
            points: list[TimelinePoint] = self.recorder.timeline_points()
            to_tick = to_tick if to_tick is not None else self.engine.world.clock.tick
            points = [p for p in points if from_tick <= p.tick <= to_tick]
            notable = [
                e.to_dict()
                for e in self.engine.store.recent_events(200, NOTABLE_TYPES)
                if from_tick <= e.tick <= to_tick
            ]
            infos = [s.model_dump() for s in self.snapshot_infos() if from_tick <= s.tick <= to_tick]
            return {"points": [p.model_dump() for p in points], "snapshots": infos, "notable_events": notable}

    @staticmethod
    def strategies() -> list[dict[str, str]]:
        with contextlib.suppress(ImportError):
            import nexus.agents.strategy
        with contextlib.suppress(ImportError):
            import nexus.optimization.strategy  # noqa: F401
        descriptions = {
            "baseline": "FIFO orders, nearest idle robot, plain A* — the reference scheduler.",
            "optimized": "CP-SAT assignment, order batching, deadline sequencing, congestion-aware routing.",
            "optimized_greedy": "Optimizer stack with greedy assignment and no batching (ablation).",
            "ai_planner": "Optimized + periodic Planner agent; top playbook plan executed without simulation.",
            "nexus_full": "Optimized + Planner + simulate-before-execute + risk gate — the full NEXUS loop.",
        }
        return [{"name": n, "description": descriptions.get(n, "")} for n in sorted(STRATEGIES)]

    def strategy_clone_bytes(self) -> bytes:
        with self.lock:
            return pickle.dumps(self.engine.strategy)
