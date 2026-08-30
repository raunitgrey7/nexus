"""Render the benchmark summary table into README.md between the BENCH markers."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "backend" / "benchmarks" / "results" / "latest.json"
README = ROOT / "README.md"
START, END = "<!-- BENCH:START -->", "<!-- BENCH:END -->"


def main() -> int:
    if not RESULTS.exists():
        print("no latest.json yet", file=sys.stderr)
        return 1
    agg = json.loads(RESULTS.read_text(encoding="utf-8"))
    cfg = agg["config"]
    rows = agg["summary_table"]
    lines = [
        START,
        "",
        f"Generated {agg['generated_at'][:16].replace('T', ' ')} UTC · {cfg['minutes']} simulated minutes per run · {cfg['seeds']} seed(s) per cell · "
        "incident schedule: robot failure at +30 min, demand surge ×1.5 at +60 min (30 min), aisle blocked at +90 min (15 min). "
        "Every strategy sees identical worlds. Full tables, definitions and charts: [docs/BENCHMARKS.md](docs/BENCHMARKS.md).",
        "",
        "| Scale | Strategy | SLA breach | Δ vs baseline | Avg fulfillment | p95 | Throughput/h | Utilization | Congestion | Sim speed |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        delta = f"{r['sla_breach_vs_baseline_pct']:+.2f} pp" if r["sla_breach_vs_baseline_pct"] is not None else "—"
        lines.append(
            f"| {r['scale']} | `{r['strategy']}` | **{r['sla_breach_pct']:.2f}%** | {delta} | {r['avg_fulfillment_min']:.2f} min | "
            f"{r['p95_fulfillment_min']:.2f} min | {r['throughput_per_hour']:.0f} | {r['robot_utilization_pct']:.1f}% | "
            f"{r['congestion_index']:.3f} | {r['ticks_per_second']:,.0f} t/s |"
        )
    charts = [p for p in ("bench_sla_breach_pct.png", "bench_avg_fulfillment_min.png", "bench_throughput_per_hour.png") if (ROOT / "pitch" / "charts" / p).exists()]
    if charts:
        lines += ["", "<p align=\"center\">"] + [f'  <img src="pitch/charts/{c}" width="49%" alt="{c}">' for c in charts[:2]] + ["</p>"]
    lines += ["", END]
    text = README.read_text(encoding="utf-8")
    i, j = text.index(START), text.index(END) + len(END)
    README.write_text(text[:i] + "\n".join(lines) + text[j:], encoding="utf-8", newline="\n")
    print(f"README benchmark block updated with {len(rows)} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
