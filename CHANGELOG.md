# Changelog

All notable changes to NEXUS are documented here. The format follows Keep a Changelog; versions follow SemVer.

## [0.1.0] — 2026-08-30

First public release.

### Added
- Digital twin core: entities, `WorldState` with fork / snapshot / deterministic digest, occupancy grid, semantic
  spatial graph, parametric warehouse layouts at four scales.
- Event engine: typed events, idempotent append-only store, pure reducer, bus with asyncio bridge, snapshot+external-event replay.
- Deterministic discrete-time simulation engine: kinematics, battery and charging, A* with congestion costs, order
  generation with Zipf demand, fault injection, KPI computation.
- Optimization engine: multi-objective scoring, CP-SAT assignment (OR-Tools) with Hungarian and greedy fallbacks,
  genetic allocator, order batching, weighted-EDF sequencing, routing policies.
- Forecasting: Holt-Winters demand forecast blended with the demand-profile prior, battery exhaustion, zone
  congestion, bottleneck detection.
- Agent runtime: situation analysis, planner (LLM + playbooks), constraint validator, optimizer, simulator
  (process-parallel forked worlds), risk agent with stability re-runs, approval policy, executor, Operations Manager.
- What-If engine with scenario DSL and presets; natural-language console with delay attribution.
- Resilient local-LLM client: structured JSON output with `$ref`-free schemas, automatic fallback model on memory errors,
  per-call timeouts and a failure cooldown so the platform degrades to deterministic planners instantly.
- FastAPI REST + WebSocket API, live runtime, PostgreSQL / SQLite persistence, Redis fan-out, Prometheus metrics,
  OpenTelemetry tracing, Grafana dashboard, Docker Compose stack.
- Next.js + Three.js twin UI: live 3D twin, decisions, what-if lab, forecast, console, timeline, benchmarks.
- Benchmark suite with incident schedule and four strategies; documentation set; pitch deck.
