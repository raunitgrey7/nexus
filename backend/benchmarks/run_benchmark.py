"""NEXUS benchmark suite.

Runs every strategy on identical worlds (same seed, same demand, same incident schedule) and reports
the shared KPIs. The incident schedule mirrors the demo: a robot failure at +30 min and a demand
surge at +60 min, so the benchmark measures *resilience*, not just steady-state throughput.

    uv run python -m benchmarks.run_benchmark --scale small medium large --minutes 120 --seeds 3

Outputs ``results/latest.json`` (+ a timestamped copy), ``../../docs/BENCHMARKS.md`` and charts in
``../../pitch/charts`` when matplotlib is available.
"""

from __future__ import annotations

import argparse
import json
import pickle
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus.agents.simulator import SimJob, run_jobs
from nexus.api.schemas import ActionModel, PlanModel
from nexus.events.types import EventType
from nexus.simulation.faults import ScheduledFault
from nexus.simulation.strategies import make_strategy
from nexus.twin import build_world, spec_for

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
DOCS = HERE.parents[1] / "docs"
CHARTS = HERE.parents[1] / "pitch" / "charts"
STRATEGIES = ["baseline", "optimized", "ai_planner", "nexus_full"]
KPI_KEYS = [
    "sla_breach_rate_projected",
    "sla_breach_rate",
    "avg_fulfillment_min",
    "p95_fulfillment_min",
    "throughput_per_hour",
    "robot_utilization",
    "congestion_index",
    "distance_total",
    "energy_total",
    "orders_delivered",
    "orders_open",
    "wait_ticks_per_robot_hour",
    "replans",
    "charging_sessions",
]
NOOP = PlanModel(
    id="bench-noop", name="benchmark", source="user", description="", actions=[ActionModel(type="NOOP")]
)


def incident_schedule(scale: str, minutes: int, world: Any) -> list[dict[str, Any]]:
    faults: list[ScheduledFault] = []
    if minutes >= 40:
        rid = "R07" if "R07" in world.robots else sorted(world.robots)[-1]
        faults.append(
            ScheduledFault(
                30 * 60,
                EventType.ROBOT_FAILURE,
                rid,
                {"cause": "motor_fault", "recovery_ticks": 45 * 60},
                f"bench:fail:{rid}",
            )
        )
    if minutes >= 75:
        faults.append(
            ScheduledFault(
                60 * 60,
                EventType.DEMAND_CHANGED,
                None,
                {"burst_multiplier": 1.5, "burst_ticks": 30 * 60},
                "bench:burst",
            )
        )
    if minutes >= 100 and scale != "tiny":
        zone = "C" if "C" in world.zones else next(iter(world.storage_zones())).id
        faults.append(
            ScheduledFault(
                90 * 60,
                EventType.AISLE_BLOCKED,
                None,
                {
                    "cells": [
                        [world.zones[zone].x0 + 3, y]
                        for y in range(world.zones[zone].y0 + 1, world.zones[zone].y1)
                    ],
                    "reason": "spill",
                },
                "bench:block",
            )
        )
        faults.append(
            ScheduledFault(
                105 * 60,
                EventType.AISLE_CLEARED,
                None,
                {
                    "cells": [
                        [world.zones[zone].x0 + 3, y]
                        for y in range(world.zones[zone].y0 + 1, world.zones[zone].y1)
                    ]
                },
                "bench:clear",
            )
        )
    return [f.to_dict() for f in faults]


def build_jobs(
    scales: list[str], strategies: list[str], seeds: int, minutes: int
) -> list[tuple[dict[str, Any], SimJob]]:
    import nexus.agents.strategy  # noqa: F401 - registers ai_planner / nexus_full

    jobs = []
    for scale in scales:
        for s in range(seeds):
            seed = 42 + s
            world = build_world(spec_for(scale, seed=seed))
            world_bytes = world.snapshot_bytes()
            faults = incident_schedule(scale, minutes, world)
            for name in strategies:
                strategy = make_strategy(name)
                meta = {
                    "scale": scale,
                    "seed": seed,
                    "strategy": name,
                    "robots": len(world.robots),
                    "zones": len(world.storage_zones()),
                    "orders_per_hour": world.demand.orders_per_hour,
                }
                jobs.append(
                    (
                        meta,
                        SimJob(
                            world_bytes,
                            pickle.dumps(strategy),
                            NOOP.model_dump(),
                            minutes * 60,
                            0,
                            faults,
                            300,
                            f"{scale}/{name}/{seed}",
                            True,
                        ),
                    )
                )
    return jobs


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"scales": {}}
    for row in rows:
        s = out["scales"].setdefault(
            row["scale"],
            {
                "strategies": {},
                "robots": row["robots"],
                "zones": row["zones"],
                "orders_per_hour": row["orders_per_hour"],
            },
        )
        entry = s["strategies"].setdefault(
            row["strategy"],
            {"runs": [], "kpis_mean": {}, "kpis_std": {}, "wall_s_mean": 0.0, "ticks_per_second": 0.0},
        )
        entry["runs"].append(row)
    for scale in out["scales"].values():
        for entry in scale["strategies"].values():
            runs = entry["runs"]
            for key in KPI_KEYS:
                vals = [float(r["kpis"][key]) for r in runs]
                entry["kpis_mean"][key] = round(statistics.fmean(vals), 5)
                entry["kpis_std"][key] = round(statistics.pstdev(vals), 5) if len(vals) > 1 else 0.0
            entry["wall_s_mean"] = round(statistics.fmean(r["wall_s"] for r in runs), 2)
            entry["ticks_per_second"] = round(statistics.fmean(r["ticks_per_second"] for r in runs), 1)
            entry["planning"] = {
                "decisions": statistics.fmean(r.get("decisions", 0) for r in runs),
                "simulated": statistics.fmean(r.get("simulated", 0) for r in runs),
            }
    return out


def summary_table(agg: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for scale, s in agg["scales"].items():
        base = s["strategies"].get("baseline", {}).get("kpis_mean", {})
        for name, entry in s["strategies"].items():
            m = entry["kpis_mean"]
            rows.append(
                {
                    "scale": scale,
                    "strategy": name,
                    "sla_breach_pct": round(100 * m["sla_breach_rate_projected"], 2),
                    "sla_breach_vs_baseline_pct": round(
                        100 * (m["sla_breach_rate_projected"] - base.get("sla_breach_rate_projected", 0)), 2
                    )
                    if base
                    else None,
                    "avg_fulfillment_min": round(m["avg_fulfillment_min"], 2),
                    "p95_fulfillment_min": round(m["p95_fulfillment_min"], 2),
                    "throughput_per_hour": round(m["throughput_per_hour"], 1),
                    "robot_utilization_pct": round(100 * m["robot_utilization"], 1),
                    "congestion_index": round(m["congestion_index"], 3),
                    "distance_per_order": round(m["distance_total"] / max(1, m["orders_delivered"]), 1),
                    "energy_per_order": round(m["energy_total"] / max(1, m["orders_delivered"]), 3),
                    "ticks_per_second": entry["ticks_per_second"],
                }
            )
    return rows


def write_markdown(agg: dict[str, Any], cfg: dict[str, Any], rows: list[dict[str, Any]], path: Path) -> None:
    lines = [
        "# Benchmarks",
        "",
        f"Generated {agg['generated_at']} · {cfg['minutes']} simulated minutes per run · {cfg['seeds']} seed(s) per cell · incident schedule: robot failure at +30 min, demand surge ×1.5 at +60 min (30 min), aisle blocked at +90 min (15 min).",
        "",
        "All strategies see identical worlds (same seed, same orders, same incidents). KPIs follow the definitions in `ROADMAP.md`.",
        "",
        "| Scale | Strategy | SLA breach | Δ vs baseline | Avg fulfillment | p95 | Throughput/h | Utilization | Congestion | Distance/order | Energy/order | Sim speed |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        delta = (
            f"{r['sla_breach_vs_baseline_pct']:+.2f} pp"
            if r["sla_breach_vs_baseline_pct"] is not None
            else "—"
        )
        lines.append(
            f"| {r['scale']} | `{r['strategy']}` | {r['sla_breach_pct']:.2f}% | {delta} | {r['avg_fulfillment_min']:.2f} min | {r['p95_fulfillment_min']:.2f} min | {r['throughput_per_hour']:.0f} | {r['robot_utilization_pct']:.1f}% | {r['congestion_index']:.3f} | {r['distance_per_order']:.1f} | {r['energy_per_order']:.3f}% | {r['ticks_per_second']:,.0f} t/s |"
        )
    lines += [
        "",
        "## Scale definitions",
        "",
        "| Scale | Robots | Storage zones | Orders/hour (base) | Grid |",
        "|---|---:|---:|---:|---|",
    ]
    for scale, s in agg["scales"].items():
        lines.append(
            f"| {scale} | {s['robots']} | {s['zones']} | {s['orders_per_hour']:.0f} | see `nexus/twin/layout.py` |"
        )
    lines += [
        "",
        "## Strategies",
        "",
        "1. `baseline` — FIFO orders, nearest idle robot, plain A*.",
        "2. `optimized` — CP-SAT assignment (OR-Tools), order batching, deadline sequencing, congestion-aware routing.",
        "3. `ai_planner` — `optimized` + Planner agent playbooks executed without simulation.",
        "4. `nexus_full` — `optimized` + Planner + simulate-before-execute on forked worlds + risk gate.",
        "",
        "Reproduce: `cd backend && uv run python -m benchmarks.run_benchmark --scale small medium large --minutes 120 --seeds 3`.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_charts(agg: dict[str, Any], rows: list[dict[str, Any]]) -> list[str]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return []
    CHARTS.mkdir(parents=True, exist_ok=True)
    written = []
    scales = list(agg["scales"])
    strategies = [s for s in STRATEGIES if any(s in agg["scales"][sc]["strategies"] for sc in scales)]
    colors = {"baseline": "#8b98a5", "optimized": "#22d3ee", "ai_planner": "#a78bfa", "nexus_full": "#22c55e"}
    for key, title, fmt in (
        ("sla_breach_pct", "Projected SLA breach (%)", "{:.1f}%"),
        ("avg_fulfillment_min", "Average fulfillment time (min)", "{:.2f}"),
        ("throughput_per_hour", "Throughput (orders / hour)", "{:.0f}"),
        ("congestion_index", "Congestion index", "{:.2f}"),
    ):
        fig, ax = plt.subplots(figsize=(8, 4.2), dpi=160)
        width = 0.8 / max(1, len(strategies))
        for i, name in enumerate(strategies):
            vals = [
                next((r[key] for r in rows if r["scale"] == sc and r["strategy"] == name), 0) for sc in scales
            ]
            xs = [j + i * width for j in range(len(scales))]
            bars = ax.bar(xs, vals, width, label=name, color=colors.get(name, "#888"))
            for b, v in zip(bars, vals, strict=True):
                ax.text(
                    b.get_x() + b.get_width() / 2,
                    b.get_height(),
                    fmt.format(v),
                    ha="center",
                    va="bottom",
                    fontsize=7,
                    color="#e6edf3",
                )
        ax.set_xticks([j + width * (len(strategies) - 1) / 2 for j in range(len(scales))])
        ax.set_xticklabels(scales)
        ax.set_title(title, color="#e6edf3")
        ax.legend(frameon=False, fontsize=8)
        fig.patch.set_facecolor("#0a0d12")
        ax.set_facecolor("#11161d")
        for spine in ax.spines.values():
            spine.set_color("#1f2933")
        ax.tick_params(colors="#8b98a5")
        ax.yaxis.label.set_color("#8b98a5")
        for text in ax.get_legend().get_texts():
            text.set_color("#e6edf3")
        fig.tight_layout()
        out = CHARTS / f"bench_{key}.png"
        fig.savefig(out, facecolor=fig.get_facecolor())
        plt.close(fig)
        written.append(str(out))
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NEXUS benchmark suite")
    parser.add_argument("--scale", nargs="+", default=["small"], choices=["tiny", "small", "medium", "large"])
    parser.add_argument("--strategies", nargs="+", default=STRATEGIES)
    parser.add_argument("--minutes", type=int, default=120)
    parser.add_argument("--seeds", type=int, default=3)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--no-docs", action="store_true")
    args = parser.parse_args(argv)

    jobs = build_jobs(args.scale, args.strategies, args.seeds, args.minutes)
    print(
        f"benchmark: {len(jobs)} runs · scales={args.scale} · strategies={args.strategies} · {args.minutes} min · seeds={args.seeds}",
        flush=True,
    )
    t0 = time.perf_counter()
    results = run_jobs([j for _, j in jobs], args.workers)
    rows = []
    for (meta, job), res in zip(jobs, results, strict=True):
        wall = res["duration_ms"] / 1000
        rows.append(
            {
                **meta,
                "kpis": res["kpis"],
                "score": res["score"],
                "wall_s": round(wall, 2),
                "ticks_per_second": round(job.horizon_ticks / max(1e-6, wall), 1),
                "diagnostics": res["diagnostics"],
                "timeline": res["timeline"],
            }
        )
        k = res["kpis"]
        print(
            f"  {meta['scale']:6s} {meta['strategy']:10s} seed={meta['seed']}  breach={k['sla_breach_rate_projected']:6.2%} avg_ft={k['avg_fulfillment_min']:5.2f} thr={k['throughput_per_hour']:6.1f} util={k['robot_utilization']:5.1%} cong={k['congestion_index']:5.2f}  [{wall:5.1f}s]",
            flush=True,
        )
    agg = aggregate(rows)
    agg["generated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    agg["config"] = {
        "minutes": args.minutes,
        "seeds": args.seeds,
        "strategies": args.strategies,
        "scales": args.scale,
        "wall_s_total": round(time.perf_counter() - t0, 1),
    }
    agg["summary_table"] = summary_table(agg)
    for scale in agg["scales"].values():
        for entry in scale["strategies"].values():
            for r in entry["runs"]:
                r["timeline"] = r["timeline"][:: max(1, len(r["timeline"]) // 40)]  # keep JSON small
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = args.out or (RESULTS / "latest.json")
    if not out.is_absolute():
        out = HERE / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(agg, indent=1), encoding="utf-8")
    stamped = RESULTS / f"bench_{datetime.now(UTC):%Y%m%d_%H%M%S}.json"
    if out.name == "latest.json":
        stamped.write_text(json.dumps(agg, indent=1), encoding="utf-8")
    print(f"wrote {out} ({time.perf_counter() - t0:.1f}s total)")
    if not args.no_docs and out.name == "latest.json":
        DOCS.mkdir(parents=True, exist_ok=True)
        write_markdown(agg, agg["config"], agg["summary_table"], DOCS / "BENCHMARKS.md")
        charts = write_charts(agg, agg["summary_table"])
        print(f"wrote docs/BENCHMARKS.md and {len(charts)} charts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
