# NEXUS — Build Roadmap

> AI-native Digital Twin & Autonomous Operations Platform.
> This file is the single source of truth for milestones. A milestone is checked off only when its
> definition-of-done is met (code + tests + docs).

## Guiding principles

1. **Deterministic core.** Same seed + same events ⇒ bit-identical world hash. Everything else is built on this.
2. **Events are the truth.** The twin is a pure reducer over an append-only event log. Replay rebuilds the world.
3. **Simulate before execute.** No plan touches the live world without a forked-world simulation and a risk verdict.
4. **The LLM proposes, mathematics disposes.** LLM output is always structured, validated, optimized, simulated and
   risk-checked before it becomes an action. The system must work (degraded, but correct) with the LLM switched off.
5. **Domain-agnostic engine, domain-specific model.** Warehouse is the first `DomainModel`; the engine never imports it.
6. **Benchmarked, not vibes.** Every strategy is measured on the same seeds, same scenarios, same KPIs.

## Milestones

| #   | Milestone                       | Definition of done                                                                                                                                      | Status |
|-----|---------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------|--------|
| M0  | Foundation                      | Monorepo, tooling (uv / ruff / mypy / pytest), Docker Compose, CI skeleton, docs skeleton                                                                | ☐ |
| M1  | Digital Twin core               | Entities, `WorldState`, spatial grid + semantic spatial graph, parametric warehouse layout, snapshot / restore, world hash                               | ☐ |
| M2  | Event engine                    | Typed events, idempotent append-only `EventStore`, reducer, replay, event bus                                                                            | ☐ |
| M3  | Simulation engine               | Deterministic discrete-time engine, kinematics + battery, A* pathfinding w/ congestion, order generator, fault injector, KPI metrics, world fork         | ☐ |
| M4  | Optimization engine             | Multi-objective cost, constraints, CP-SAT assignment (OR-Tools) w/ Hungarian + greedy fallbacks, GA allocator, EDF scheduling, congestion-aware routing | ☐ |
| M5  | Forecasting                     | Demand (Holt-Winters), battery exhaustion, zone congestion, bottleneck detection                                                                         | ☐ |
| M6  | Agent runtime                   | Ops Manager, Forecaster, Planner (LLM + deterministic fallback), Constraint Validator, Optimizer, Simulator, Risk, Approval policy, Executor; decision records | ☐ |
| M7  | What-If engine                  | Scenario DSL (remove robots, demand spike, zone closure, dock closure, charger reduction, inventory move), parallel evaluation, comparison report        | ☐ |
| M8  | API, persistence, observability | FastAPI REST + WebSocket live stream, Postgres event / snapshot / decision persistence, Redis pub/sub, Prometheus metrics, OpenTelemetry, live runtime loop | ☐ |
| M9  | Twin UI                         | Next.js + TypeScript + Three.js 3D twin, KPI bar, event feed, decision / plan panel, what-if lab, NLQ console, timeline playback, comparison charts     | ☐ |
| M10 | Benchmarks                      | 4 strategies × 3 scales × N seeds; KPIs; results JSON + charts + `docs/BENCHMARKS.md`                                                                    | ☐ |
| M11 | Documentation                   | README, ARCHITECTURE, DIGITAL_TWIN, SIMULATION, OPTIMIZATION, AGENTS, SAFETY, WHAT_IF, API, DOMAIN_EXTENSION, ADRs                                       | ☐ |
| M12 | Ship                            | Public GitHub repo, CI green, release tag                                                                                                                | ☐ |
| M13 | Pitch deck                      | Investor / company deck with charts, tables, trees, benchmark graphs, roadmap, business model                                                            | ☐ |

## KPI definitions (used everywhere: metrics, benchmarks, UI, deck)

| KPI                  | Definition                                                                    |
|----------------------|-------------------------------------------------------------------------------|
| Avg fulfillment time | mean(delivered_at − created_at) over delivered orders                         |
| SLA breach rate      | share of completed orders with delivered_at > deadline                        |
| Throughput           | delivered orders per simulated hour                                           |
| Robot utilization    | share of robot-ticks in a productive state (moving to pick / deliver, picking)|
| Distance traveled    | total cells traveled by all robots                                            |
| Energy consumed      | total battery % consumed across the fleet                                     |
| Congestion index     | mean over ticks of Σ_zones max(0, robots_in_zone − zone_capacity)             |
| Planning latency     | wall-clock seconds to produce a decision (plan → optimize → simulate → risk)  |
| Simulation fidelity  | |simulated KPI − realized KPI| for executed plans                             |

## Strategies benchmarked

1. `baseline`   — FIFO orders, nearest-idle-robot assignment, shortest path.
2. `optimized`  — CP-SAT assignment (multi-objective), congestion-aware routing, EDF sequencing.
3. `ai_planner` — Planner agent (LLM or deterministic fallback) chooses strategy parameters + reprioritisation; heuristic execution.
4. `nexus_full` — Planner → Optimizer → simulate K candidate plans in forked worlds → Risk → pick best → Execute.
