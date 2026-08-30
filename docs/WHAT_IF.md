# What-If engine

The What-If engine (`nexus/whatif`) answers "what happens if…?" by forking the live world, applying a scenario as
scheduled faults, running one forked simulation per strategy (and per seed), and comparing the results on the shared
KPIs and the optimization score — next to a reference run of the unmodified world. Scenarios come from presets, from the
UI's scenario builder, or from natural-language questions. This document covers the scenario DSL, the presets, how runs
are compared, the narrative, asynchronous jobs and the NL bridge.

```
CURRENT STATE ──fork──► + scenario mutations ──► strategy A ──► KPIs
                ├──fork──► + scenario mutations ──► strategy B ──► KPIs
                └──fork──► (no mutation, current strategy) ──► reference KPIs
```

## Scenario DSL (`nexus/whatif/scenarios.py`)

A `ScenarioModel(name, description, mutations[])` holds `MutationModel(type, params, at_min)` entries; `at_min` is the
offset in minutes after the scenario starts. `scenario_faults(world, scenario, start_tick)` translates each mutation into
`ScheduledFault`s (external events with idempotent keys `scenario:{i}:{type}:…`), so what-if runs use exactly the same
fault machinery as the live twin.

| `type` | `params` | Becomes |
|---|---|---|
| `ROBOT_FAILURE` | `{robot_ids: [...] \| robot_id, cause, recovery_min}` | `ROBOT_FAILURE` per robot (default R07, motor_fault, 45 min) |
| `REMOVE_ROBOTS` / `ADD_ROBOTS` | `{count}` or `{robot_ids}` | `ROBOT_REMOVED` (highest-id operational robots first) / `ROBOT_ADDED` in the charging bay |
| `DEMAND_MULTIPLIER` | `{multiplier}` | `DEMAND_CHANGED(multiplier)` |
| `DEMAND_BURST` | `{multiplier, duration_min}` | `DEMAND_CHANGED(burst_multiplier, burst_ticks)` |
| `CLOSE_ZONE` | `{zone_id, reopen_min?}` | `ZONE_CLOSED` (+ `ZONE_OPENED` later) |
| `CLOSE_DOCK` | `{dock_id}` | `DOCK_CLOSED` |
| `DISABLE_CHARGERS` | `{count}` or `{charger_ids}` | `CHARGER_DISABLED` per station |
| `BLOCK_AISLE` | `{cells}` or `{zone_id, aisles, clear_min?}` | `AISLE_BLOCKED` (+ `AISLE_CLEARED`) — aisles are the zone's first aisle columns |
| `MOVE_INVENTORY` | `{from_zone, to_zone, skus, units}` | `INVENTORY_MOVED` payloads from `OptimizationEngine.reposition_inventory_events` |
| `WORKER_DELAY` | `{worker_ids?, minutes}` | `WORKER_DELAY` |
| `SET_SLA` | `{LOW\|NORMAL\|HIGH\|CRITICAL: minutes}` | `CONFIG_CHANGED(sla_minutes)` |
| `SET_BATCHING` | `{orders_per_trip}` | `CONFIG_CHANGED(batch_max_orders)` |

`describe_scenario` renders a scenario as prose ("R07 fails; demand ×1.30 at +5 min").

## Presets (`nexus/whatif/presets.py`, `GET /api/whatif/presets`)

| id | Question |
|---|---|
| `demand-plus-40` | What happens if order volume increases by 40%? |
| `demand-double-30` | What if demand doubles for the next 30 minutes? |
| `r07-failure` | What if robot R07 fails right now? |
| `remove-2-robots` | What if we remove two robots? |
| `add-2-robots` | What if we add two robots? |
| `zone-b-closed` | What if Zone B is inaccessible for an hour? |
| `dock-d2-closed` | What if loading dock 2 closes? |
| `chargers-half` | What if charging capacity is reduced by half? |
| `aisle-block-c` | What if an aisle in Zone C is blocked for 30 minutes? |
| `reposition-c-to-b` | What if we move the hottest SKUs from Zone C to Zone B? |
| `batching-3` | What if robots batch three orders per trip? |
| `failure-plus-demand` | What if R07 fails while demand is up 30%? |

## Running and comparing (`nexus/whatif/engine.py`)

`WhatIfEngine.run(WhatIfRequest)`:

1. Fork the live world, read the current strategy (pickled) and the live engine's pending scheduled faults.
2. Build one `SimJob` per requested strategy × seed (`seed s > 0` derives a new random stream, salt `101·s`) with the live
   faults **plus** the scenario faults, and — when `include_current` — a reference job with no scenario and the current
   strategy. A strategy equal to the live one reuses the live instance's configuration (routing policy, batching …).
3. Execute all jobs through `nexus.agents.simulator.run_jobs` (process pool when available).
4. Convert each result into a `WhatIfRun` (KPIs over the horizon, `delta_vs_reference`, timeline, duration) and aggregate
   per strategy (`comparison` rows: mean SLA breach, fulfillment, p95, throughput, utilization, congestion, open orders
   at the end, **score**).
5. Sort the comparison by score (the optimization objective, lower is better); `best_strategy` is the first row.

Horizon: `horizon_min` (5–480, default 90). Seeds: 1–5. Strategies must be registered (`GET /api/strategies`).

## Narrative

The narrative is deterministic: scenario name and description, horizon, number of strategies/seeds and wall time; the
reference KPIs; one sentence per strategy; and the best strategy with the spread between best and worst SLA breach,
e.g. *"Best strategy: nexus_full — SLA breach 3.1% vs 21.0% for baseline."*

## Asynchronous jobs

`POST /api/whatif` returns a `WhatIfResult` with `status: "queued"` immediately and runs the evaluation on a daemon
thread; poll `GET /api/whatif/{id}` until `status` is `done` (or `failed` with `error`). `GET /api/whatif` lists recent
results, and the live WebSocket pushes a `{"type": "whatif", "result": …}` frame on completion. `POST /api/whatif/run`
is the synchronous variant used by the console and the CLI (`uv run nexus whatif --preset demand-plus-40`).

## From natural language to scenarios

The console (`docs/NLQ.md`) classifies "what if …" questions and extracts parameters (percentages, robot counts, robot
ids, zone letters, dock ids, minutes, verbs such as *fail/remove/add/close/double*), then `build_scenario(params, world)`
assembles the mutations — e.g. "What if we remove two robots and demand doubles for 15 minutes?" becomes
`REMOVE_ROBOTS(count=2)` + `DEMAND_BURST(×2, 15 min)`. Unparseable hypotheticals default to the signature R07 failure.
The console then runs the current strategy, `optimized` and `nexus_full` against the scenario and answers with the
narrative plus the projected breach under the current strategy versus today and versus the best alternative.
