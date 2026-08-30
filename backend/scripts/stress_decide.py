"""Stress scenario for the decision pipeline: peak hour + demand surge + R07 failure."""

import sys
import time

from nexus.agents.ops_manager import OperationsManager
from nexus.events.types import EventType
from nexus.forecasting import Forecaster, HistoryRecorder
from nexus.llm.client import NullLLM
from nexus.simulation.engine import SimulationEngine
from nexus.simulation.faults import FaultInjector
from nexus.simulation.strategies import make_strategy
from nexus.twin import build_world, spec_for


def main() -> None:
    warm = int(sys.argv[1]) if len(sys.argv) > 1 else 150
    burst = float(sys.argv[2]) if len(sys.argv) > 2 else 1.4
    horizon = int(sys.argv[3]) if len(sys.argv) > 3 else 60

    world = build_world(spec_for("small", seed=42))
    eng = SimulationEngine(world, make_strategy("optimized"), fault_injector=FaultInjector())
    rec = HistoryRecorder()
    eng.hooks.append(rec.hook)
    t0 = time.perf_counter()
    eng.run(warm * 60 - 300)
    eng.inject(
        EventType.DEMAND_CHANGED, None, {"burst_multiplier": burst, "burst_ticks": 3600}, origin="user"
    )
    eng.run(300)
    k = eng.kpis(since_tick=eng.world.clock.tick - 1800)
    print(
        f"{world.clock.now():%H:%M} before failure: open={k.orders_open} breach(30m)={k.sla_breach_rate_projected:.1%} util={k.robot_utilization:.0%} [{(time.perf_counter() - t0):.0f}s]"
    )
    eng.inject(
        EventType.ROBOT_FAILURE, "R07", {"cause": "motor_fault", "recovery_ticks": 2700}, origin="user"
    )
    eng.run(30)
    ops = OperationsManager(eng, NullLLM(), Forecaster(), rec, candidate_plans=8, horizon_ticks=horizon * 60)
    d = ops.decide(trigger="ROBOT_FAILURE:R07")
    base = d.baseline
    print(
        f"baseline: breach={base.kpis.sla_breach_rate_projected:.1%} avg={base.kpis.avg_fulfillment_min:.2f} thr={base.kpis.throughput_per_hour:.0f} score={base.score:.2f}"
    )
    for p in sorted(d.candidates, key=lambda p: p.rank or 99):
        s = p.simulation
        mark = "*" if p.id == d.recommended_plan_id else " "
        print(
            f"{mark} #{p.rank or '-'} {p.name[:58]:58s} breach={s.kpis.sla_breach_rate_projected:6.1%} avg={s.kpis.avg_fulfillment_min:5.2f} thr={s.kpis.throughput_per_hour:4.0f} cong={s.kpis.congestion_index:.2f} score={s.score:6.2f} risk={p.risk.level if p.risk else '-'}"
        )
    print(d.explanation)
    rec_plan = next(p for p in d.candidates if p.id == d.recommended_plan_id)
    for f in rec_plan.risk.findings if rec_plan.risk else []:
        print(f"  risk[{f.severity}] {f.kind}: {f.message}")
    print(
        "timings:",
        {k: round(v) for k, v in d.timings.items()},
        "| candidates_evaluated:",
        d.candidates_evaluated,
        "| allocations:",
        d.situation.get("allocations_considered"),
    )


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
