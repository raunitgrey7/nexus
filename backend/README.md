# NEXUS backend

The Python engine behind NEXUS: the digital twin, the event-sourced world, the deterministic simulation, the optimization
and forecasting engines, the agent runtime, the what-if engine, the natural-language console, and the FastAPI/WebSocket
API. See the repository root `README.md` and `docs/` for the full picture.

## Package map

| Package | Contents |
|---|---|
| `nexus/core` | `config.py` (settings, `NEXUS_*` env), `logging.py`, `clock.py`, `rng.py`, `ids.py` |
| `nexus/twin` | `entities.py`, `world.py` (`WorldState`), `spatial.py` (`GridMap`, `SpatialGraph`), `layout.py` (scales), `domain.py` |
| `nexus/events` | `types.py`, `store.py`, `bus.py`, `reducer.py`, `replay.py` |
| `nexus/simulation` | `engine.py`, `pathfinding.py`, `tasks.py`, `order_generator.py`, `faults.py`, `metrics.py`, `strategies.py` |
| `nexus/optimization` | `objective.py`, `constraints.py`, `batching.py`, `scheduling.py`, `assignment.py`, `genetic.py`, `routing.py`, `engine.py`, `strategy.py`, `quick_compare.py` |
| `nexus/forecasting` | `history.py`, `smoothing.py`, `demand.py`, `battery.py`, `congestion.py`, `bottleneck.py`, `forecaster.py` |
| `nexus/agents` | `situation.py`, `planner.py`, `validator.py`, `executor.py`, `simulator.py`, `risk.py`, `policy.py`, `explain.py`, `ops_manager.py`, `strategy.py` |
| `nexus/whatif` | `scenarios.py`, `presets.py`, `engine.py` |
| `nexus/nlq` | `router.py`, `explain.py`, `service.py` |
| `nexus/llm` | `client.py`, `prompts.py`, `rag.py` |
| `nexus/runtime` | `live.py` (`LiveRuntime`) |
| `nexus/api` | `app.py`, `routes/core.py`, `routes/intelligence.py`, `ws.py`, `schemas.py`, `deps.py` |
| `nexus/persistence` | `db.py`, `redis_pub.py` |
| `nexus/observability` | `metrics.py`, `tracing.py` |
| `benchmarks/` | `run_benchmark.py` (+ `results/`) |
| `scripts/` | `smoke_twin.py`, `smoke_sim.py`, `calibrate.py` |
| `tests/` | pytest suite |

## Setup

```bash
uv sync --group dev --extra bench      # Python 3.13 venv (pinned in .python-version), OR-Tools, FastAPI, …
```

## Commands (`uv run nexus --help`)

| Command | Purpose | Main options |
|---|---|---|
| `nexus api` | run the API + live twin (uvicorn) | `--host 0.0.0.0 --port 8000 --reload --strategy optimized --scale small --seed 42` |
| `nexus run` | headless simulation, prints the KPI table | `--scale --minutes 120 --strategy optimized --seed 42 --fail-robot R07 --fail-at-min 30` |
| `nexus decide` | warm up, fail a robot, run the full decision pipeline once | `--scale --warmup-min 60 --fail-robot R07 --horizon-min 90 --candidates 8 --llm --seed --out decision.json` |
| `nexus whatif` | run a what-if preset across strategies | `--preset demand-plus-40 --scale small --warmup-min 45 --horizon-min 60 --seed 42` |
| `nexus demo` | the pitch storyline in the terminal | `--scale small --seed 42 --llm` |
| `nexus bench` | the benchmark suite (`benchmarks/run_benchmark.py`) | `--scale small --scale medium --minutes 120 --seeds 3 --out results/latest.json --workers 4` |
| `nexus world` | dump a freshly built world as JSON (UI fixtures) | `--scale small --seed 42 --out world.json` |

`python -m nexus …` and `python -m benchmarks.run_benchmark …` work as well.

## Environment variables

All optional (defaults suit a laptop demo); see `../.env.example`.

| Variable | Default | Meaning |
|---|---|---|
| `NEXUS_DATABASE_URL` | unset (in-memory) | e.g. `postgresql+asyncpg://nexus:nexus@localhost:5432/nexus` or `sqlite+aiosqlite:///./nexus.db` |
| `NEXUS_REDIS_URL` | unset | e.g. `redis://localhost:6379/0` — publishes live frames on `nexus:live` |
| `NEXUS_SNAPSHOT_EVERY_TICKS` | 600 | world snapshot cadence |
| `NEXUS_LLM_ENABLED` / `NEXUS_OLLAMA_URL` / `NEXUS_LLM_MODEL` | true / `http://localhost:11434` / `qwen2.5:7b` | local LLM |
| `NEXUS_LLM_TIMEOUT_S` / `NEXUS_LLM_TEMPERATURE` | 90 / 0.2 | |
| `NEXUS_DEFAULT_SCALE` / `NEXUS_DEFAULT_SEED` | small / 42 | world built at startup |
| `NEXUS_LIVE_TICKS_PER_SECOND` | 10 | live loop speed (changeable at runtime) |
| `NEXUS_CANDIDATE_PLANS` / `NEXUS_SIM_HORIZON_TICKS` | 8 / 5400 | decision pipeline |
| `NEXUS_RISK_SEEDS` / `NEXUS_DECISION_WORKERS` | 2 / 4 | stability re-runs; simulation worker processes |
| `NEXUS_AUTO_APPROVE_MAX_RISK` / `NEXUS_AUTO_APPROVE_MIN_GAIN` | LOW / 0.02 | approval policy |
| `NEXUS_API_HOST` / `NEXUS_API_PORT` / `NEXUS_CORS_ORIGINS` | 0.0.0.0 / 8000 / localhost:3000 | API |
| `NEXUS_PROMETHEUS_ENABLED` / `NEXUS_OTEL_ENABLED` / `NEXUS_OTEL_ENDPOINT` | true / false / unset | observability |
| `NEXUS_LOG_LEVEL` / `NEXUS_LOG_JSON` | INFO / false | logging |

## Tests and quality gates

```bash
uv run pytest -q                          # 92 tests (~2–3 min; `-m "not slow"` skips the 3-hour small-scale run)
uv run ruff check . && uv run ruff format --check .
uv run mypy nexus
uv run python scripts/smoke_sim.py small 3600     # determinism + fork + replay gate
uv run python scripts/calibrate.py small 5400 300,400,500   # demand calibration sweep
```

Markers: `slow` (long simulations), `llm` (requires a running Ollama; excluded in CI).
