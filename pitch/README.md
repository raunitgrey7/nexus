# NEXUS pitch deck

`NEXUS_Pitch_Deck.pptx` — a 33-slide, 16:9 investor / company deck built entirely from measured data by
`build_deck.py`. Dark control-room theme, speaker notes on every slide.

## Rebuild

```bash
cd backend
uv run --with python-pptx --with matplotlib python ../pitch/build_deck.py
```

The script is deterministic and idempotent: it overwrites the `.pptx` and `charts/deck_*.png`, then re-opens the file
and prints the slide list. Dependencies beyond the backend environment are listed in `requirements.txt`.

To refresh the measured inputs (≈ 3–5 minutes of simulation on a laptop):

```bash
cd backend
uv run python ../pitch/capture_measurements.py     # → data/sweep.json, data/nlq_examples.json
uv run python -m benchmarks.run_benchmark --scale small medium large --minutes 120 --seeds 3   # → results/latest.json
```

## What feeds which slide

| Slides | Source |
|---|---|
| 1–3, 6, 7, 14 (demo numbers) | `DEMO` constants in `build_deck.py` — the output of `uv run nexus demo` on 2026-08-30 (calibrated small world, 10:30 peak, ×1.2 surge, R07 motor fault): 39.4 % → 3.1 % projected SLA breach, 9 plans / 21 allocations in 18.8 s |
| 9 (scale table) | `SCALES` — mirrors `backend/nexus/twin/layout.py` |
| 11 (ticks/s), 24 (tests), 12–13 (timings) | `PERF` constants — engine speed, forecast and fork timings, test counts measured during development |
| 17, 23 (what-if bars, capacity cliff) | `data/sweep.json` — peak-hour demand sweep produced by `capture_measurements.py` with the real engine (small world, flat ×1.2 profile, 90 min, with and without an R07 failure) |
| 18 (console examples) | `data/nlq_examples.json` — grounded answers of the natural-language console captured on a stressed world with the LLM off |
| 20–22 (benchmarks, radar, table) | `backend/benchmarks/results/latest.json` (full run) — falls back to `results/sample.json` (tiny scale, generated automatically) and labels the charts "sample run" |
| 25 (tech stack donut) | line counts measured by walking the repository at build time |
| everything else | `README.md`, `docs/*.md`, `docs/API.md`, `ROADMAP.md`, `docker-compose.yml`, `frontend/README.md` |

Charts: matplotlib PNGs in `charts/` (dark theme) for the timeline, candidate ranking, benchmark bars, radar,
capacity cliff and what-if comparison; native PowerPoint doughnut charts for the test and line-count breakdowns
(editable in PowerPoint).

## Slide map

1 Title · 2 The problem · 3 Normal system vs NEXUS · 4 Vision · 5 Product in one picture · 6 Demo timeline ·
7 The decision · 8 Architecture · 9 Digital twin · 10 Event-sourced world · 11 Deterministic simulation ·
12 Optimization · 13 Forecasting · 14 Multi-agent runtime · 15 Safety architecture · 16 Spatial AI · 17 What-If engine ·
18 Natural-language console · 19 Visualization · 20–22 Benchmarks I–III · 23 The capacity cliff · 24 Engineering
quality · 25 Tech stack · 26 Domain expansion · 27 Business model & GTM · 28 Competitive landscape · 29 Roadmap ·
30 Team & portfolio · 31 The ask · 32–33 Appendix (KPI definitions, action vocabulary, API surface).

`preview/` (ignored by git) can be regenerated with PowerPoint installed:

```bash
cd backend && uv run --with pywin32 python -c "import win32com.client,os;a=win32com.client.Dispatch('PowerPoint.Application');p=a.Presentations.Open(os.path.abspath('../pitch/NEXUS_Pitch_Deck.pptx'),WithWindow=False);p.Export(os.path.abspath('../pitch/preview'),'PNG',1600,900);p.Close();a.Quit()"
```
