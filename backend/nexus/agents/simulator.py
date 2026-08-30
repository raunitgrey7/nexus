"""Simulator agent: evaluates plans (and what-if scenarios) in forked worlds.

A :class:`SimJob` is fully serialisable (world bytes, pickled strategy, plan dict, scheduled faults),
so jobs run either in-process or in a persistent :class:`ProcessPoolExecutor` — the engine is pure
Python, so processes (not threads) are what buy parallelism. Results are plain dicts converted to
:class:`SimulationOutcome`.
"""

from __future__ import annotations

import os
import pickle
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from nexus.api.schemas import PlanModel, SimulationOutcome, TimelinePoint
from nexus.core.logging import get_logger
from nexus.events.types import EventType
from nexus.simulation.faults import FaultInjector, ScheduledFault
from nexus.simulation.metrics import KPIs, compute_kpis
from nexus.twin.entities import RobotStatus
from nexus.twin.world import WorldState

log = get_logger("nexus.agents.simulator")
_POOL: ProcessPoolExecutor | None = None


@dataclass(slots=True)
class SimJob:
    world_bytes: bytes
    strategy_bytes: bytes
    plan: dict[str, Any]
    horizon_ticks: int
    seed_salt: int = 0
    faults: list[dict[str, Any]] = field(default_factory=list)
    sample_every: int = 60
    label: str = ""
    spontaneous_faults: bool = True


def _score(kpis: KPIs) -> float:
    try:
        from nexus.optimization.objective import score_kpis

        return float(score_kpis(kpis))
    except Exception:
        return round(
            100 * kpis.sla_breach_rate_projected + kpis.avg_fulfillment_min + 5 * kpis.congestion_index, 4
        )


def run_job(job: SimJob) -> dict[str, Any]:
    """Execute one simulation job. Module-level so it can run in a worker process."""
    from nexus.agents.executor import PlanExecutor
    from nexus.simulation.engine import SimulationEngine

    t0 = time.perf_counter()
    world = WorldState.from_snapshot(job.world_bytes)
    strategy = pickle.loads(job.strategy_bytes)
    if job.seed_salt:
        world.rng = world.rng.derive(job.seed_salt)
    faults = [
        ScheduledFault(
            int(f["tick"]),
            EventType(f["type"]),
            f.get("entity_id"),
            dict(f.get("payload", {})),
            f.get("key"),
            f.get("origin", "scenario"),
        )
        for f in job.faults
    ]
    engine = SimulationEngine(
        world, strategy, fault_injector=FaultInjector(faults, spontaneous=job.spontaneous_faults)
    )
    plan = PlanModel.model_validate(job.plan)
    executor = PlanExecutor(engine)
    applied = executor.execute(plan)
    start = world.clock.tick
    end = start + job.horizon_ticks
    k0 = compute_kpis(world, since_tick=start)
    timeline: list[dict[str, Any]] = [
        {
            "tick": start,
            "open": k0.orders_open,
            "delivered": 0,
            "breach_projected": k0.sla_breach_rate_projected,
            "congestion": world.congestion_total(),
            "utilization": 0.0,
        }
    ]
    max_wait = 0
    min_battery = 100.0
    zone_max_ratio = 0.0
    stuck_ticks = 0
    charger_starved = 0
    overcap_ticks = 0
    while world.clock.tick < end:
        engine.step()
        elapsed = world.clock.tick - start
        for r in world.robots.values():
            if r.status.operational:
                if r.wait_ticks > max_wait:
                    max_wait = r.wait_ticks
                if r.battery < min_battery:
                    min_battery = r.battery
                if r.wait_ticks >= 20:
                    stuck_ticks += 1
                if (
                    r.status == RobotStatus.IDLE
                    and r.battery < world.config.battery_low_threshold
                    and r.charger_id is None
                ):
                    charger_starved += 1
        tick_max = 0.0
        for z in world.zones.values():
            occ = world.zone_occupancy.get(z.id, 0)
            if z.kind.value in ("storage", "corridor") and occ > 0:
                ratio = occ / max(1, z.capacity)
                if ratio > tick_max:
                    tick_max = ratio
        if tick_max > zone_max_ratio:
            zone_max_ratio = tick_max
        if tick_max >= 2.0:
            overcap_ticks += 1
        if elapsed % job.sample_every == 0 or world.clock.tick == end:
            k = compute_kpis(world, since_tick=start)
            timeline.append(
                {
                    "tick": world.clock.tick,
                    "open": k.orders_open,
                    "delivered": k.orders_delivered,
                    "breach_projected": k.sla_breach_rate_projected,
                    "congestion": world.congestion_total(),
                    "utilization": k.robot_utilization,
                }
            )
    kpis = compute_kpis(world, since_tick=start)
    counts = engine.store.counts
    return {
        "label": job.label,
        "horizon_ticks": job.horizon_ticks,
        "kpis": kpis.to_dict(),
        "score": _score(kpis),
        "timeline": timeline,
        "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
        "diagnostics": {
            "max_wait_ticks": float(max_wait),
            "min_battery": round(min_battery, 2),
            "zone_max_ratio": round(zone_max_ratio, 3),
            "overcap_ticks": float(overcap_ticks),
            "overcap_share": round(overcap_ticks / max(1, job.horizon_ticks), 4),
            "stuck_robot_ticks": float(stuck_ticks),
            "charger_starved_ticks": float(charger_starved),
            "stockouts": float(
                sum(
                    1
                    for e in engine.store.log
                    if e.type.value == "TASK_CANCELLED" and e.payload.get("reason") == "stockout"
                )
            ),
            "failures": float(counts.get("ROBOT_FAILURE", 0)),
            "replans": float(world.stats.replans_total),
            "events": float(engine.store.seq),
            "allocations": float(executor.allocations_evaluated),
        },
        "events_applied": len(applied),
        "digest": world.digest(),
    }


def _pool(workers: int) -> ProcessPoolExecutor:
    global _POOL
    if _POOL is None:
        _POOL = ProcessPoolExecutor(max_workers=workers)
    return _POOL


def run_jobs(jobs: list[SimJob], workers: int | None = None) -> list[dict[str, Any]]:
    """Run jobs, in parallel processes when it pays off; falls back to sequential on any pool error."""
    if not jobs:
        return []
    workers = workers if workers is not None else max(1, min(os.cpu_count() or 2, 4))
    if workers > 1 and len(jobs) >= 3:
        try:
            pool = _pool(workers)
            return list(pool.map(run_job, jobs))
        except Exception as exc:
            log.warning("simulator.pool_failed", error=str(exc)[:200])
    return [run_job(job) for job in jobs]


def to_outcome(result: dict[str, Any], baseline: dict[str, Any] | None = None) -> SimulationOutcome:
    kpis = result["kpis"]
    delta: dict[str, float] = {}
    if baseline is not None:
        bk = baseline["kpis"]
        for key in (
            "sla_breach_rate_projected",
            "avg_fulfillment_min",
            "throughput_per_hour",
            "robot_utilization",
            "congestion_index",
            "p95_fulfillment_min",
        ):
            delta[key] = round(float(kpis[key]) - float(bk[key]), 5)
        delta["score"] = round(float(result["score"]) - float(baseline["score"]), 5)
    return SimulationOutcome(
        horizon_ticks=int(
            result.get("horizon_ticks")
            or (
                result["timeline"][-1]["tick"] - result["timeline"][0]["tick"]
                if len(result["timeline"]) > 1
                else 0
            )
        ),
        kpis=kpis,
        delta_vs_baseline=delta,
        score=float(result["score"]),
        timeline=[TimelinePoint(**p) for p in result["timeline"]],
        duration_ms=float(result["duration_ms"]),
        diagnostics=dict(result.get("diagnostics", {})),
        events_applied=int(result.get("events_applied", 0)),
    )


class SimulatorAgent:
    def __init__(self, workers: int | None = None, sample_every: int = 60) -> None:
        self.workers = workers
        self.sample_every = sample_every
        self.last_batch_ms = 0.0

    def make_jobs(
        self,
        world: WorldState,
        strategy: Any,
        plans: list[PlanModel],
        horizon_ticks: int,
        faults: list[ScheduledFault] | None = None,
        seed_salts: list[int] | None = None,
    ) -> list[SimJob]:
        world_bytes = world.snapshot_bytes()
        strategy_bytes = pickle.dumps(strategy)
        fault_dicts = [f.to_dict() for f in (faults or [])]
        salts = seed_salts or [0] * len(plans)
        return [
            SimJob(
                world_bytes,
                strategy_bytes,
                plan.model_dump(),
                horizon_ticks,
                salt,
                fault_dicts,
                self.sample_every,
                plan.id,
            )
            for plan, salt in zip(plans, salts, strict=True)
        ]

    def simulate(
        self,
        world: WorldState,
        strategy: Any,
        plans: list[PlanModel],
        horizon_ticks: int,
        faults: list[ScheduledFault] | None = None,
        seed_salts: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        t0 = time.perf_counter()
        results = run_jobs(
            self.make_jobs(world, strategy, plans, horizon_ticks, faults, seed_salts), self.workers
        )
        self.last_batch_ms = round((time.perf_counter() - t0) * 1000, 1)
        return results
