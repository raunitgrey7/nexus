"""Calibration sweep: find demand levels at which the baseline runs near capacity."""

import sys
import time

from nexus.simulation.engine import SimulationEngine
from nexus.simulation.strategies import GreedyStrategy
from nexus.twin import build_world, spec_for

scale = sys.argv[1] if len(sys.argv) > 1 else "small"
ticks = int(sys.argv[2]) if len(sys.argv) > 2 else 5400
rates = [float(x) for x in sys.argv[3].split(",")] if len(sys.argv) > 3 else [150, 200, 250, 300, 350, 400]

for rate in rates:
    world = build_world(spec_for(scale, orders_per_hour=rate))
    world.demand.hourly_multipliers = [1.0] * 24  # flat for calibration
    eng = SimulationEngine(world, GreedyStrategy())
    t0 = time.perf_counter()
    eng.run(ticks)
    dt = time.perf_counter() - t0
    k = eng.kpis()
    print(
        f"{scale} rate={rate:5.0f}/h  created={k.orders_created:5d} delivered={k.orders_delivered:5d} open={k.orders_open:4d} "
        f"avg_ft={k.avg_fulfillment_min:5.2f}m p95={k.p95_fulfillment_min:5.2f}m breach={k.sla_breach_rate:6.2%} "
        f"proj={k.sla_breach_rate_projected:6.2%} util={k.robot_utilization:6.2%} cong={k.congestion_index:5.2f} "
        f"wait/rh={k.wait_ticks_per_robot_hour:5.1f} charges={k.charging_sessions} [{ticks / dt:.0f} t/s]"
    )
