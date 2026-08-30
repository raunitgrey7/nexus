"""Smoke test for the simulation engine: run, KPIs, determinism, fork divergence, replay."""

import sys
import time

from nexus.events.replay import verify_replay
from nexus.events.types import EventType
from nexus.simulation.engine import SimulationEngine
from nexus.simulation.faults import FaultInjector, ScheduledFault
from nexus.simulation.strategies import GreedyStrategy
from nexus.twin import build_world, spec_for

scale = sys.argv[1] if len(sys.argv) > 1 else "small"
ticks = int(sys.argv[2]) if len(sys.argv) > 2 else 3600


def make(seed: int = 42, faults: list[ScheduledFault] | None = None) -> SimulationEngine:
    world = build_world(spec_for(scale, seed=seed))
    return SimulationEngine(world, GreedyStrategy(), fault_injector=FaultInjector(faults or []))


t0 = time.perf_counter()
eng = make()
snapshot = eng.world.snapshot_bytes()
eng.run(ticks)
dt = time.perf_counter() - t0
k = eng.kpis()
print(f"[{scale}] {ticks} ticks in {dt:.2f}s ({ticks / dt:.0f} ticks/s)")
print(
    f"  created={k.orders_created} delivered={k.orders_delivered} open={k.orders_open} pending={k.orders_pending} "
    f"late={k.orders_late} overdue_open={k.orders_overdue_open}"
)
print(
    f"  avg_ft={k.avg_fulfillment_min:.2f}min p95={k.p95_fulfillment_min:.2f} sla_breach={k.sla_breach_rate:.3%} "
    f"projected={k.sla_breach_rate_projected:.3%} thr={k.throughput_per_hour:.1f}/h util={k.robot_utilization:.2%} "
    f"cong={k.congestion_index:.3f} wait/rh={k.wait_ticks_per_robot_hour:.1f} replans={k.replans} charges={k.charging_sessions}"
)
print("  events:", eng.store.stats()["persisted"], "pathfinder:", eng.pathfinder.stats())
statuses = {}
for r in eng.world.robots.values():
    statuses[r.status.value] = statuses.get(r.status.value, 0) + 1
print(
    "  robot statuses:",
    statuses,
    "batteries:",
    [round(r.battery) for r in list(eng.world.robots.values())[:12]],
)

# determinism
eng2 = make()
eng2.run(ticks)
print("  deterministic:", eng.world.digest() == eng2.world.digest())

# fork at mid-run then continue both: identical
eng3 = make()
eng3.run(ticks // 2)
fork = eng3.world.fork("test")
eng_fork = SimulationEngine(fork, GreedyStrategy())
eng3.run(ticks // 2)
eng_fork.run(ticks // 2)
print("  fork identical:", eng3.world.digest() == eng_fork.world.digest())

# replay with an injected failure at tick 600 and aisle block at 900
faults = [
    ScheduledFault(600, EventType.ROBOT_FAILURE, "R07", {"cause": "motor_fault", "recovery_ticks": 1800}),
    ScheduledFault(900, EventType.AISLE_BLOCKED, None, {"cells": [[8, 5], [8, 6]], "reason": "spill"}),
]
eng4 = make(faults=faults)
snap4 = eng4.world.snapshot_bytes()
eng4.run(ticks)
ok = verify_replay(eng4, snap4, lambda w: SimulationEngine(w, GreedyStrategy()))
print(
    "  replay verified:",
    ok,
    "| external events:",
    len(eng4.store.external_events()),
    "| failures:",
    eng4.world.stats.failures_total,
)
