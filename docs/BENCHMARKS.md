# Benchmarks

Generated 2026-08-30T19:12:10+00:00 · 120 simulated minutes per run · 3 seed(s) per cell · incident schedule: robot failure at +30 min, demand surge ×1.5 at +60 min (30 min), aisle blocked at +90 min (15 min).

All strategies see identical worlds (same seed, same orders, same incidents). KPIs follow the definitions in `ROADMAP.md`.

| Scale | Strategy | SLA breach | Δ vs baseline | Avg fulfillment | p95 | Throughput/h | Utilization | Congestion | Distance/order | Energy/order | Sim speed |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| small | `baseline` | 41.69% | +0.00 pp | 9.43 min | 26.70 min | 316 | 81.4% | 0.049 | 87.4 | 1.810% | 2,996 t/s |
| small | `optimized` | 2.84% | -38.85 pp | 3.60 min | 10.55 min | 390 | 80.0% | 0.055 | 67.5 | 1.400% | 2,402 t/s |
| small | `ai_planner` | 3.07% | -38.61 pp | 3.19 min | 8.05 min | 395 | 79.7% | 0.035 | 66.3 | 1.375% | 2,313 t/s |
| small | `nexus_full` | 1.80% | -39.88 pp | 2.81 min | 5.67 min | 408 | 77.9% | 0.049 | 66.0 | 1.371% | 490 t/s |
| medium | `baseline` | 39.98% | +0.00 pp | 9.04 min | 24.46 min | 826 | 79.6% | 0.873 | 114.2 | 2.365% | 833 t/s |
| medium | `optimized` | 1.47% | -38.51 pp | 2.94 min | 5.69 min | 1010 | 76.3% | 0.650 | 88.1 | 1.827% | 636 t/s |
| medium | `ai_planner` | 1.80% | -38.19 pp | 2.94 min | 5.69 min | 1009 | 75.9% | 0.541 | 88.0 | 1.826% | 621 t/s |
| medium | `nexus_full` | 1.47% | -38.51 pp | 3.03 min | 6.72 min | 1006 | 75.7% | 0.632 | 88.1 | 1.828% | 112 t/s |
| large | `baseline` | 37.57% | +0.00 pp | 8.64 min | 22.16 min | 1512 | 72.7% | 1.575 | 152.9 | 3.168% | 261 t/s |
| large | `optimized` | 1.72% | -35.85 pp | 2.90 min | 5.14 min | 1804 | 64.5% | 1.251 | 112.9 | 2.350% | 209 t/s |
| large | `ai_planner` | 1.63% | -35.94 pp | 2.90 min | 5.09 min | 1804 | 64.6% | 1.243 | 113.1 | 2.354% | 210 t/s |
| large | `nexus_full` | 1.54% | -36.03 pp | 2.89 min | 5.12 min | 1804 | 64.5% | 1.243 | 112.9 | 2.350% | 47 t/s |

## Scale definitions

| Scale | Robots | Storage zones | Orders/hour (base) | Grid |
|---|---:|---:|---:|---|
| small | 12 | 12 | 400 | see `nexus/twin/layout.py` |
| medium | 40 | 24 | 1000 | see `nexus/twin/layout.py` |
| large | 100 | 50 | 1800 | see `nexus/twin/layout.py` |

## Strategies

1. `baseline` — FIFO orders, nearest idle robot, plain A*.
2. `optimized` — CP-SAT assignment (OR-Tools), order batching, deadline sequencing, congestion-aware routing.
3. `ai_planner` — `optimized` + Planner agent playbooks executed without simulation.
4. `nexus_full` — `optimized` + Planner + simulate-before-execute on forked worlds + risk gate.

Reproduce: `cd backend && uv run python -m benchmarks.run_benchmark --scale small medium large --minutes 120 --seeds 3`.
