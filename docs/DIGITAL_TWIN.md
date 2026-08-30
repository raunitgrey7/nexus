# Digital Twin

The digital twin is the single source of truth: a `WorldState` holding every entity of the operation (zones, shelves,
robots, workers, orders, tasks, docks, chargers), an occupancy grid for movement, and a semantic spatial graph for
reasoning. It can be forked, snapshotted and hashed, and it is only ever mutated by the event reducer. This document
covers the entity model, the world state and its digest, the layout generator, the built-in scales, the spatial graph and
snapshots.

## Entity model (`nexus/twin/entities.py`)

Entities are plain `dataclass(slots=True)` objects — fast to copy, trivial to hash — with `to_dict()` for the API and
`from_dict()` where the reducer needs to hydrate them from event payloads.

| Entity | Fields (abridged) | Notes |
|---|---|---|
| `Zone` | `id, name, kind, x0, y0, x1, y1, capacity, closed` | kinds: `storage`, `corridor`, `dock`, `charging`, `staging`; `capacity` = robots before congestion |
| `Shelf` | `id, cell, access_cell, zone_id, inventory{sku: qty}` | `access_cell` is the walkable cell a robot stands on to pick |
| `ChargingStation` | `id, cell, zone_id, slots, enabled, occupants[]` | `free_slots` is 0 when disabled |
| `LoadingDock` | `id, cell, zone_id, open, queue[], delivered` | deliveries happen on the dock cell |
| `Worker` | `id, name, role, cell, zone_id, status, delay_until_tick` | roles: loader, packer, picker, supervisor; a dock without an active loader unloads 2× slower |
| `Order` | `id, created_tick, deadline_tick, priority, lines[OrderLine], status, task_id, robot_id, dock_id, started/delivered/cancelled_tick` | priorities `LOW/NORMAL/HIGH/CRITICAL`; statuses `pending → assigned → in_progress → delivered` (or `cancelled`) |
| `Task` | `id, robot_id, order_ids[], waypoints[Waypoint], leg, status, origin` | one robot, one or more orders (a batch); waypoint kinds `pick`, `deliver`, `charge`, `move` |
| `Robot` | `id, cell, zone_id, battery, status, task_id, path[], speed, capacity, load, action_until_tick, wait_ticks, distance, energy, failure_cause, recover_at_tick, charger_id` | statuses `idle, moving, picking, delivering, unloading, to_charger, charging, waiting, failed, maintenance` |
| `DemandProfile` | `orders_per_hour, hourly_multipliers[24], multiplier, burst_*, priority_weights, max_lines, max_qty` | lives inside the world so forks carry it |
| `SimConfig` | speeds, battery constants, pick/unload durations, SLA minutes, replenishment, batching | see `docs/SIMULATION.md` |
| `RunningStats` | monotonic counters maintained by the reducer | KPIs derive from these + entity state |

Example robot (`GET /api/world/entity/R07`):

```json
{ "id": "R07", "cell": [42, 17], "zone_id": "C", "battery": 63.2, "status": "moving",
  "task_id": "TASK-000812", "path": [[43, 17], [44, 17]], "speed": 1.0, "capacity": 10, "load": 3 }
```

## World state (`nexus/twin/world.py`)

`WorldState` groups the entity dictionaries with the `GridMap`, the `SimClock`, the `SeededRNG`, the `IdGen` counters,
the demand profile, the config, the running stats, a `version` counter (incremented per applied event) and free-form
`labels`. Derived caches (`occupancy: cell → robot ids`, `zone_occupancy`, the open-order index, zone adjacency) are
rebuilt on restore and never hashed.

* `fork(label)` — pickle round-trip (≈5× faster than `deepcopy`), sets `is_fork`, rebuilds caches.
* `snapshot_bytes()` / `from_snapshot()` — the same bytes, used by snapshots, replay and simulation jobs.
* `digest()` — SHA-256 over the grid digest and a canonical JSON of tick, version, id counters, a hash of the RNG state,
  robots (cell, battery to 4 dp, status, task, load, distance, charger), orders, tasks, chargers, docks, shelf inventory,
  zones, workers, stats, demand and config. Two worlds with the same digest are behaviourally identical.
* `summary()` — the compact status used by the API/WS `hello` frame.
* `to_dict(orders="open"|"all"|"none", include_grid=True)` — the world snapshot shape in `docs/API.md`.
* Helpers used by engines and agents: `open_orders()`, `pending_orders()`, `available_robots()`, `operational_robots()`,
  `active_tasks()`, `open_docks()`, `storage_zones()`, `zone_congestion(zone)`, `congestion_total()`, `cell_free(cell)`,
  `place_robot(robot, cell)` (reducer only).

## Grid, zones and layout (`nexus/twin/spatial.py`, `nexus/twin/layout.py`)

`GridMap` is a dense `bytearray` of cell types (`FLOOR=0, SHELF=1, WALL=2, DOCK=3, CHARGER=4, CONVEYOR=5, STAGING=6`) plus
a zone id per cell, a set of dynamically blocked cells (aisle blockages) and a set of closed zones. Walkable types are
floor, dock, charger and staging. `GridMap.version` increments on every walkability change and keys the pathfinder's
caches.

The warehouse layout generator builds the world from a `WarehouseSpec`:

```
y ▲
  │ ┌──────┬──┬──────────┬──┬──────────┬──┐
  │ │ CHG  │C │  Zone E  │C │  Zone F  │C │   storage zones: 10×10 cells, three 2-wide shelf strips
  │ │      │  ├──────────┴──┴──────────┴──┤   with 1-cell aisles between them (pattern F S S F S S F S S F)
  │ │      │  │        corridor            │   corridors: 2 cells wide, their own zones (C1, C2 …)
  │ │      │  ├──────────┬──┬──────────┬──┤   charging bay: left strip (chargers on x = 0)
  │ │      │  │  Zone A  │C │  Zone B  │C │   loading docks: bottom strip (docks on y = 0, staging above)
  │ ├──────┴──┴──────────┴──┴──────────┴──┤
  │ │             DOCKS / STAGING          │
  └─┴──────────────────────────────────────┴──▶ x
```

* Storage zones are lettered row-major from the bottom-left (`A, B, C, …`, then `AA` …); corridors are numbered
  (`C1…`, horizontal first, then vertical); the charging bay is `CHG`, the dock strip `DOCK`.
* Every shelf has an `access_cell` in the adjacent aisle; shelves per storage zone = 8 rows × 6 = 48.
* SKUs follow a Zipf popularity (`1/(i+1)^0.8`), get 1–3 shelf copies, and receive stock **proportional to popularity**
  so fast movers do not starve; sparse shelves are topped up so every shelf holds 3 SKUs.
* Zone capacities: storage `max(3, round(2·robots/zones))`, corridor `max(4, round(3·robots/corridors))`, charging bay
  `robots + 2` (parking never counts as congestion), dock strip `3 × docks`.
* Robots start in the charging bay with staggered batteries (65–100 %); workers stand at the docks.

### Scales (`SCALES` in `layout.py`)

| Scale | Storage zones | Grid | Shelves | Robots | Workers | Docks | Chargers (×2 slots) | SKUs | Units | Base orders/h |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `tiny` | 2×2 = 4 | 29×29 | 192 | 4 | 3 | 2 | 2 | 120 | 4,000 | 90 |
| `small` | 4×3 = 12 | 53×41 | 576 | 12 | 7 | 4 | 4 | 600 | 18,000 | 400 |
| `medium` | 6×4 = 24 | 77×53 | 1,152 | 40 | 16 | 8 | 10 | 2,000 | 60,000 | 1,000 |
| `large` | 10×5 = 50 | 125×65 | 2,400 | 100 | 30 | 16 | 24 | 5,000 | 150,000 | 1,800 |

`spec_for(scale, **overrides)` and `build_world(spec)` are deterministic for a given seed; `get_domain("warehouse")`
exposes the same through the `DomainModel` interface.

## Spatial graph (`SpatialGraph`)

The semantic layer is a `networkx.DiGraph` derived from the world on demand — the "spatial AI" the agents and the
console reason with. Relations (edge attribute `rel`):

| Relation | Subject → object |
|---|---|
| `adjacent_to` | zone ↔ zone (geometric adjacency of walkable cells, computed once per grid version) |
| `located_in` | shelf / dock / charger → zone |
| `is_inside` | robot / worker → zone |
| `assigned_to` | task → robot |
| `serves` | task → order |
| `requires` | order → shelf |
| `requires_zone` | order → zone |
| `charging_at` | robot → charger |
| `ships_from` | order → dock |

Queries: `relations_of(id)`, `adjacent_zones(zone, include_closed=False)`, `entities_in_zone(zone, kind)`,
`orders_requiring_zone(zone)`, `zone_route(from, to, avoid=…)` (BFS over adjacency honouring closures), `zone_load()`,
`describe(id)` (human-readable triples used in prompts and explanations), `to_dict()` (`GET /api/spatial`).

```python
sg = SpatialGraph(world)
sg.adjacent_zones("C")        # ['C1', 'C2', 'C7', 'C8']
sg.zone_route("A", "L")       # ['A', 'C1', 'C8', 'L']
sg.describe("R07")            # ['R07 is_inside C', 'TASK-000812 assigned_to R07']
```

## Snapshots

The live runtime keeps a bounded ring (`deque(maxlen=120)`) of `(tick, digest, headline KPIs, bytes)` taken every
`NEXUS_SNAPSHOT_EVERY_TICKS` (600 by default) and emits `SNAPSHOT_TAKEN`. `GET /api/timeline` lists them,
`GET /api/snapshots/{tick}` rehydrates one for playback, and replay (`nexus/events/replay.py`) rebuilds any later state
from a snapshot plus the external events recorded after it. With `NEXUS_DATABASE_URL` set, snapshots, decisions and
what-if results are also persisted (`nexus/persistence/db.py`).
