"""Capture the measured numbers that feed the pitch deck (peak-hour capacity sweep + NL console examples).

Run from the backend environment:

    cd backend && uv run python ../pitch/capture_measurements.py

Writes ``pitch/data/sweep.json`` and ``pitch/data/nlq_examples.json``. Everything here is produced by the real
engine on the calibrated small world (12 robots), so the deck never contains invented figures.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"


def capacity_sweep() -> dict:
    """Projected SLA breach vs demand at the late-morning peak (flat x1.2 profile, 90 simulated minutes)."""
    import nexus.optimization.strategy  # noqa: F401 - registers the optimized strategy
    from nexus.events.types import EventType
    from nexus.simulation.engine import SimulationEngine
    from nexus.simulation.faults import FaultInjector, ScheduledFault
    from nexus.simulation.strategies import make_strategy
    from nexus.twin import build_world, spec_for

    rows = []
    for base in (300, 340, 380, 420, 480, 540, 600):
        row: dict = {"orders_per_hour": round(base * 1.2)}
        for strat in ("baseline", "optimized"):
            for fail in (False, True):
                world = build_world(spec_for("small", seed=42, orders_per_hour=base))
                world.demand.hourly_multipliers = [1.2] * 24
                faults = (
                    [ScheduledFault(1800, EventType.ROBOT_FAILURE, "R07", {"cause": "motor_fault", "recovery_ticks": 2700})]
                    if fail
                    else []
                )
                eng = SimulationEngine(world, make_strategy(strat), fault_injector=FaultInjector(faults))
                t0 = time.perf_counter()
                eng.run(5400)
                k = eng.kpis()
                key = f"{strat}_{'fail' if fail else 'ok'}"
                row[key] = {
                    "sla_breach_pct": round(100 * k.sla_breach_rate_projected, 2),
                    "avg_fulfillment_min": round(k.avg_fulfillment_min, 2),
                    "p95_fulfillment_min": round(k.p95_fulfillment_min, 2),
                    "throughput_per_hour": round(k.throughput_per_hour, 1),
                    "utilization_pct": round(100 * k.robot_utilization, 1),
                    "ticks_per_second": round(5400 / (time.perf_counter() - t0)),
                }
                print(f"  {row['orders_per_hour']:4d}/h {strat:9s} fail={fail!s:5s} breach={k.sla_breach_rate_projected:6.2%}", flush=True)
        rows.append(row)
    return {
        "description": "Small world (12 robots), flat x1.2 demand profile, 90 simulated minutes, seed 42; 'fail' = R07 motor fault at +30 min (45 min recovery). Projected SLA breach = late delivered + open-overdue over delivered + open.",
        "rows": rows,
    }


def nlq_examples() -> dict:
    """Grounded answers of the natural-language console on a stressed small world (deterministic, no LLM)."""
    from nexus.events.types import EventType
    from nexus.llm.client import NullLLM
    from nexus.nlq.service import NLQService
    from nexus.runtime.live import LiveRuntime

    rt = LiveRuntime(scale="small", seed=42, strategy="optimized", llm=NullLLM(), workers=1)
    rt.step(140 * 60)  # 10:20
    rt.inject("DEMAND_CHANGED", None, {"burst_multiplier": 1.2, "burst_ticks": 3600}, key="deck-burst")
    rt.step(5 * 60)
    rt.inject(EventType.ROBOT_FAILURE.value, "R07", {"cause": "motor_fault", "recovery_ticks": 2700}, key="deck-fail")
    rt.step(4 * 60)
    svc = NLQService(rt)
    out = []
    for q, horizon in (
        ("How many orders are open right now?", 60),
        ("Why are orders slowing down?", 60),
        ("What happens if order volume increases by 40%?", 45),
        ("Where is R03 and what is it doing?", 60),
    ):
        r = svc.ask(q, horizon_min=horizon, use_llm=False)
        out.append({"question": q, "intent": r.intent, "answer": r.answer, "latency_ms": r.latency_ms})
        print(f"  [{r.intent}] {q} -> {r.answer[:110]}...", flush=True)
    rt.close()
    return {"description": "Captured at 10:29 sim time after a x1.2 surge and an R07 failure; deterministic answers (LLM off).", "examples": out}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    DATA.mkdir(parents=True, exist_ok=True)
    print("capacity sweep…", flush=True)
    (DATA / "sweep.json").write_text(json.dumps(capacity_sweep(), indent=1), encoding="utf-8")
    print("NL console examples…", flush=True)
    (DATA / "nlq_examples.json").write_text(json.dumps(nlq_examples(), indent=1, ensure_ascii=False), encoding="utf-8")
    print("wrote", DATA)


if __name__ == "__main__":
    main()
