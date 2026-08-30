"""Quick baseline-vs-optimized comparison (used by tests, docs and ``python -m``)."""

from __future__ import annotations

import argparse
import json
import time
from typing import Any

from nexus.optimization.objective import score_kpis
from nexus.simulation.engine import SimulationEngine
from nexus.simulation.faults import FaultInjector
from nexus.simulation.strategies import make_strategy
from nexus.twin import build_world, spec_for

HEADLINE = (
    "sla_breach_rate_projected",
    "avg_fulfillment_min",
    "p95_fulfillment_min",
    "throughput_per_hour",
    "robot_utilization",
    "congestion_index",
)


def run_strategy(name: str, scale: str = "small", minutes: int = 60, seed: int = 42) -> dict[str, Any]:
    world = build_world(spec_for(scale, seed=seed))
    strategy = make_strategy(name)
    engine = SimulationEngine(world, strategy, fault_injector=FaultInjector(spontaneous=False))
    ticks = int(minutes * 60 / world.clock.tick_seconds)
    t0 = time.perf_counter()
    engine.run(ticks)
    dt = time.perf_counter() - t0
    kpis = engine.kpis()
    return {
        "strategy": strategy.describe(),
        "kpis": kpis.to_dict(),
        "score": score_kpis(kpis),
        "ticks": ticks,
        "seconds": round(dt, 3),
        "ticks_per_s": round(ticks / dt, 1) if dt > 0 else 0.0,
        "digest": world.digest(),
    }


def compare_strategies(
    scale: str = "small",
    minutes: int = 60,
    seed: int = 42,
    strategies: tuple[str, ...] = ("baseline", "optimized"),
) -> dict[str, Any]:
    out: dict[str, Any] = {name: run_strategy(name, scale, minutes, seed) for name in strategies}
    ref = out[strategies[0]]["kpis"]
    out["delta_vs_" + strategies[0]] = {
        name: {k: round(out[name]["kpis"][k] - ref[k], 5) for k in HEADLINE} for name in strategies[1:]
    }
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="baseline vs optimized quick comparison")
    parser.add_argument("--scale", default="small")
    parser.add_argument("--minutes", type=int, default=60)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--strategies", nargs="+", default=["baseline", "optimized"])
    args = parser.parse_args()
    result = compare_strategies(args.scale, args.minutes, args.seed, tuple(args.strategies))
    for name in args.strategies:
        k = result[name]["kpis"]
        print(  # noqa: T201
            f"{name:18s} breach={k['sla_breach_rate_projected']:7.2%} avg_ft={k['avg_fulfillment_min']:5.2f}m "
            f"p95={k['p95_fulfillment_min']:5.2f}m thr={k['throughput_per_hour']:6.1f}/h util={k['robot_utilization']:6.1%} "
            f"cong={k['congestion_index']:5.2f} score={result[name]['score']:7.2f} [{result[name]['ticks_per_s']:.0f} t/s]"
        )
    print(json.dumps(result["delta_vs_" + args.strategies[0]], indent=2))  # noqa: T201


if __name__ == "__main__":
    main()
