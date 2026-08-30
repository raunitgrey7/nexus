# NEXUS API

Base URL: `http://localhost:8000` · OpenAPI: `/docs` · Prometheus: `/metrics` · WebSocket: `ws://localhost:8000/ws/live`

All bodies are JSON. Shapes are defined once in `backend/nexus/api/schemas.py` (pydantic) and mirrored in
`frontend/src/lib/types.ts`.

## Conventions

* `tick` — integer simulated seconds since the world epoch (`sim_time` is the ISO timestamp).
* Cells are `[x, y]` integer pairs; the origin is bottom-left; `grid.rows[y][x]` gives the cell type digit
  (`0` floor · `1` shelf · `2` wall · `3` dock · `4` charger · `5` conveyor · `6` staging).
* KPI fields are identical everywhere (`KPIModel`): `sla_breach_rate_projected` is the headline number.

## REST

| Method | Path | Body → Response | Purpose |
|---|---|---|---|
| GET | `/api/health` | → `{status, version, tick, llm}` | liveness + LLM reachability |
| GET | `/api/status` | → `SimStatus` | running flag, tick, speed, strategy, autopilot |
| POST | `/api/sim/control` | `SimControlRequest` → `SimStatus` | `start` · `pause` · `step` · `reset` · `speed` |
| GET | `/api/world` | `?orders=open\|all\|none&grid=true` → world snapshot | full twin state (`summary, clock, zones, robots, workers, docks, chargers, orders, tasks, grid, shelves, stats, demand, config, zone_occupancy`) + `kpis` |
| GET | `/api/world/robots` | → `Robot[]` | light-weight robot list |
| GET | `/api/world/orders` | `?status=&limit=` → `Order[]` | order list |
| GET | `/api/world/entity/{id}` | → entity dict + `relations` | any entity by id (robot, order, zone, shelf, dock, charger, worker, task) |
| GET | `/api/world/relations/{id}` | → `EntityRelations` | semantic triples of one entity (`R07 is_inside C`, …) |
| GET | `/api/kpis` | `?since_tick=` → `KPIModel` | KPIs over the whole run or a window |
| GET | `/api/spatial` | → `SpatialResponse` | semantic spatial graph (nodes, edges, zone load, adjacency) |
| GET | `/api/events` | `?since_seq=&limit=&types=A,B` → `EventModel[]` | persisted events after a sequence number |
| GET | `/api/events/recent` | `?limit=&notable=true` → `EventModel[]` | ring buffer (includes ephemeral) |
| POST | `/api/events/inject` | `InjectEventRequest` → `EventModel` | inject an external event (idempotent by `key`) |
| GET | `/api/faults/presets` | → `FaultPreset[]` | one-click demo faults (R07 failure, aisle spill, dock closure…) |
| POST | `/api/faults/{preset_id}` | → `EventModel` | fire a preset |
| GET | `/api/forecast` | `?horizon_min=` → `Forecast` | demand, battery, congestion, bottlenecks |
| POST | `/api/decisions` | `DecisionRequest` → `DecisionModel` | run the full agent pipeline (plan → validate → optimize → simulate → risk → approve) |
| GET | `/api/decisions` | `?limit=` → `DecisionModel[]` | decision history (newest first) |
| GET | `/api/decisions/{id}` | → `DecisionModel` | one decision |
| POST | `/api/decisions/{id}/actions` | `DecisionActionRequest` → `DecisionModel` | `approve` / `reject` / `execute` (execute requires approved) |
| POST | `/api/whatif` | `WhatIfRequest` → `WhatIfResult` (`status: queued`) | start a what-if evaluation (async) |
| POST | `/api/whatif/run` | `WhatIfRequest` → `WhatIfResult` (`status: done`) | synchronous variant — waits for the result |
| GET | `/api/whatif` | → `WhatIfResult[]` | history |
| GET | `/api/whatif/{id}` | → `WhatIfResult` | poll until `status: done` |
| GET | `/api/whatif/presets` | → `WhatIfPreset[]` | canned questions ("What if demand rises 40%?") |
| POST | `/api/nlq` | `NLQRequest` → `NLQResponse` | natural-language console |
| GET | `/api/timeline` | `?from_tick=&to_tick=` → `TimelineResponse` | KPI timeline points, snapshot list, notable events |
| GET | `/api/snapshots/{tick}` | → world snapshot dict | world as it was at a snapshot tick (playback) |
| GET | `/api/benchmarks` | → benchmark results JSON | latest `backend/benchmarks/results/latest.json` |
| GET | `/api/strategies` | → `{name, description}[]` | available scheduling strategies |
| GET | `/metrics` | → Prometheus text | `nexus_*` metrics |

Errors follow FastAPI conventions: `{ "detail": "..." }` with 4xx/5xx.

## WebSocket `/ws/live`

Server → client JSON frames (`type` discriminates):

```jsonc
{ "type": "hello",    "world": { /* GET /api/world payload */ }, "kpis": { /* KPIModel */ }, "status": { /* SimStatus */ } }
{ "type": "tick",     "tick": 1234, "sim_time": "...", "robots": [ { "id": "R07", "cell": [12, 8], "status": "moving", "battery": 63.2, "task_id": "TASK-000123", "path": [[13,8],[14,8]] } ],
                       "kpis": { "sla_breach_rate_projected": 0.041, "avg_fulfillment_min": 3.9, "throughput_per_hour": 310, "robot_utilization": 0.77, "congestion_index": 0.06, "orders_open": 41 },
                       "zone_occupancy": { "C": 4 }, "docks": [ { "id": "D1", "queue": 1, "open": true } ], "chargers": [ { "id": "CH01", "occupants": ["R03"] } ] }
{ "type": "event",    "event": { /* EventModel */ } }                 // notable + order/task events (never ROBOT_MOVED)
{ "type": "decision", "decision": { /* DecisionModel */ } }           // whenever a decision is created/updated
{ "type": "forecast", "forecast": { /* Forecast */ } }                // every ~60 simulated seconds
{ "type": "whatif",   "result": { /* WhatIfResult */ } }              // when a what-if job finishes
{ "type": "status",   "status": { /* SimStatus */ } }                 // on start/pause/reset/speed
```

Client → server frames:

```jsonc
{ "type": "control", "action": "start" | "pause" | "step" | "speed", "ticks_per_second": 20 }
{ "type": "subscribe", "tick_every": 2 }     // throttle tick frames (default 1 frame per engine tick, capped at ~20/s)
{ "type": "ping" }                            // → { "type": "pong" }
```

`tick` frames are throttled to at most ~20 per wall-second; each frame carries the *current* robot positions
(not deltas), so the client never desynchronises.

## Action vocabulary (plans)

| `type` | `params` | Effect when executed |
|---|---|---|
| `REASSIGN_TASKS` | `{ "from_robots": ["R07"], "to_robots": ["R03","R09"], "zones": ["B","C"], "max_tasks": 14 }` | cancel + rebuild tasks with the optimizer restricted to `to_robots` |
| `REPRIORITIZE_ORDERS` | `{ "priority_at_least": "HIGH", "boost_minutes": 5 }` or `{ "zones": ["C"], ... }` | tightens deadlines / boosts sequencing weight |
| `SEND_TO_CHARGE` | `{ "robot_ids": ["R04"], "after_current_task": true }` | pre-emptive charging |
| `REROUTE_AVOID_ZONE` | `{ "zones": ["C"], "penalty": 6.0, "duration_min": 30 }` | congestion-aware routing penalty |
| `PREFER_CORRIDOR` | `{ "corridors": ["C4"], "bonus": 0.4, "duration_min": 30 }` | routing bias |
| `REPOSITION_INVENTORY` | `{ "from_zone": "C", "to_zone": "B", "skus": 6, "units": 40 }` | moves hottest SKUs of a zone |
| `SET_BATCHING` | `{ "orders_per_trip": 3 }` | enables multi-order trips |
| `SET_ZONE_CAPACITY` | `{ "zones": {"C": 2} }` | soft capacities for congestion routing |
| `CLOSE_ZONE` / `OPEN_ZONE` | `{ "zone_id": "C" }` | closes / opens a zone |
| `ADD_ROBOTS` / `REMOVE_ROBOTS` | `{ "count": 2 }` / `{ "robot_ids": [...] }` | fleet size |
| `DISPATCH_WORKER` | `{ "worker_id": "W03", "dock_id": "D2" }` | move a worker |
| `CANCEL_TASKS` | `{ "task_ids": [...] }` | cancel tasks (orders go back to pending) |
| `SET_STRATEGY` | `{ "name": "optimized" }` | switch the live scheduling strategy |
| `NOOP` | `{}` | do nothing (reference plan) |

## Scenario mutations (what-if)

| `type` | `params` |
|---|---|
| `ROBOT_FAILURE` | `{ "robot_ids": ["R07"], "cause": "motor_fault", "recovery_min": 45 }` |
| `REMOVE_ROBOTS` / `ADD_ROBOTS` | `{ "count": 2 }` or `{ "robot_ids": [...] }` |
| `DEMAND_MULTIPLIER` | `{ "multiplier": 1.4 }` |
| `DEMAND_BURST` | `{ "multiplier": 2.0, "duration_min": 30 }` |
| `CLOSE_ZONE` | `{ "zone_id": "B" }` |
| `CLOSE_DOCK` | `{ "dock_id": "D2" }` |
| `DISABLE_CHARGERS` | `{ "count": 2 }` or `{ "charger_ids": [...] }` |
| `BLOCK_AISLE` | `{ "cells": [[8,5],[8,6]] }` or `{ "zone_id": "C", "aisles": 1 }` |
| `MOVE_INVENTORY` | `{ "from_zone": "C", "to_zone": "B", "skus": 6, "units": 40 }` |
| `WORKER_DELAY` | `{ "worker_ids": ["W01"], "minutes": 30 }` |
| `SET_SLA` | `{ "NORMAL": 8, "HIGH": 4 }` |
| `SET_BATCHING` | `{ "orders_per_trip": 3 }` |
