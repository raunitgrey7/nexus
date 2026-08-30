# Simulation

`nexus/simulation` is a deterministic discrete-time engine: one `step()` is one tick (one simulated second by default),
the order of operations inside a tick is fixed, all randomness comes from the world's seeded RNG, and the engine never
mutates the world directly — it reads state and **emits events** that the reducer applies. The same engine runs the live
twin and every forked simulation world. This document lists the tick order, the kinematics/battery/picking rules and
their constants, the congestion model, order generation, replenishment, fault injection, the KPI definitions, the event
vocabulary and the replay model.

## Tick order of operations (`SimulationEngine.step`)

1. `OrderGenerator.tick` — Poisson arrivals → `ORDER_CREATED`.
2. `FaultInjector.tick` — scheduled scenario faults (injected as external events, idempotent by key) and seeded
   spontaneous failures (`robot_failure_rate_per_hour`).
3. Recoveries — failed robots whose `recover_at_tick` has passed → `ROBOT_RECOVERED`.
4. Workers — delayed workers whose delay expired → `WORKER_STATUS_CHANGED`.
5. Replenishment — every `replenish_every_ticks` (300) SKUs below `replenish_threshold` (6 units) are restocked to
   `replenish_target` (24) on their best shelf → `INVENTORY_RESTOCKED`.
6. `strategy.tick` — the scheduler decides assignments → `TASK_CREATED` (and pre-emptive charging for the optimizer).
7. Robot kinematics, in robot-id order (see below).
8. Hooks (`engine.hooks`, e.g. the `HistoryRecorder`).
9. `TICK` event carrying congestion / productive / operational counts (the reducer updates `RunningStats` and applies
   idle battery drain), then the clock advances.

## Kinematics, battery and picking (`SimulationEngine._advance_robot`)

| Rule | Behaviour |
|---|---|
| Movement | one cell per tick at `robot_speed` (1.0); path from the strategy's `route()` (A*); `ROBOT_MOVED` per cell |
| Cell capacity | floor cells hold 2 robots, corridor cells 4, dock/charger/staging cells are shared; a robot that cannot enter waits (`ROBOT_WAITING`) |
| Replanning | after `max_wait_before_replan` (6) consecutive waits the robot re-routes around the blocking cell; a goal unreachable for `unreachable_cancel_ticks` (60) cancels the task (orders return to the pending pool) |
| Congested zone | when the robot's zone is over capacity its speed is multiplied by `congestion_speed_factor` (0.6) — implemented as a deterministic tick-skip (`floor(t·v) == floor((t−1)·v)`), no fractional state |
| Picking | at a `pick` waypoint the robot spends `pick_ticks` (6) then `ITEM_PICKED` (inventory decremented, line marked picked, order `in_progress`) |
| Unloading | at the `deliver` waypoint `unload_ticks` (4), ×`unload_no_loader_factor` (2.0) if no active loader is at the dock; every fully picked order → `ORDER_DELIVERED`, then `ITEM_DROPPED`, then `TASK_COMPLETED` |
| Battery drain | `battery_drain_move` 0.02 %/cell, `battery_drain_action` 0.01 %/action tick, `battery_drain_idle` 0.0005 %/tick |
| Low battery | an idle robot below `battery_low_threshold` (20 %) reserves the nearest enabled charger with a free slot → `BATTERY_LOW`, `ROBOT_STATUS_CHANGED(to_charger)`; on arrival `CHARGING_STARTED`; charges at `battery_charge_rate` (0.15 %/tick) until `battery_charge_target` (90 %) → `CHARGING_COMPLETED` |
| Depletion | battery ≤ 0 → `ROBOT_FAILURE(cause="battery_depleted")` with `failure_recovery_minutes` (30) recovery |
| Task feasibility | `make_task` refuses trips whose estimated battery need (`cells·drain_move + picks·pick_ticks·drain_action`, × `battery_reserve_factor` 1.3, + ½ threshold) exceeds the robot's battery |
| Failures | `ROBOT_FAILURE` cancels the robot's task, re-shelves picked items and returns its orders to pending; `ROBOT_RECOVERED` after `recovery_ticks` |
| Stock-outs | a pick attempt with insufficient stock cancels the task (`reason="stockout"`); `resolve_lines` re-targets order lines to shelves with stock in open zones |

Actions are modelled with an absolute `action_until_tick` rather than countdowns, which keeps the engine free of per-tick
scratch mutations (the world changes only through the reducer).

## Order generation (`OrderGenerator`)

* Arrivals per tick are `Poisson(λ)` with
  `λ = orders_per_hour · hourly_multiplier[hour] · multiplier · burst_multiplier(if tick < burst_until_tick) · tick_seconds / 3600`.
* The default profile is a 10-hour operating window (08:00–18:00) ramping from 0.3 at 07:00 to a late-morning peak of
  1.25 at 11:00, a lunch dip, an afternoon peak of 1.2 and a wind-down; nights sit at 0.05. The world epoch is Monday
  08:00.
* Each order has 1–4 lines (weights 0.45/0.30/0.15/0.10), SKUs drawn by Zipf popularity (bisect on cumulative weights),
  quantity 1–`max_qty` (2), the shelf with the most stock, and a priority drawn from `priority_weights`
  (LOW 15 %, NORMAL 70 %, HIGH 12 %, CRITICAL 3 %). Deadlines are `created + sla_minutes[priority]`
  (LOW 20, NORMAL 10, HIGH 5, CRITICAL 3 minutes).

## Fault injection (`FaultInjector`)

`ScheduledFault(tick, type, entity_id, payload, key, origin="scenario")` entries are injected when their tick arrives
(idempotent by key, so replays and re-runs never double-apply). Spontaneous failures draw a cause from
`motor_fault, lidar_fault, wheel_slip, controller_reboot, battery_cell_fault, collision_stop` with a recovery time of
`failure_recovery_minutes × U(0.6, 1.6)`. The live runtime's fault presets (`GET /api/faults/presets`) inject the same
event types by hand.

## Pathfinding (`Pathfinder`)

A* over flat cell indices with the Manhattan heuristic, an optional per-cell extra-cost function (routing policies and
congestion), an `avoid` set for replanning, and a path cache keyed by `(start, goal)` that is invalidated when
`grid.version` changes. `bfs_distances(start)` returns an exact distance field (cached), which the optimizer uses to
build robot × batch cost matrices without running A* per pair.

## KPIs (`nexus/simulation/metrics.py`)

| KPI | Definition |
|---|---|
| `avg_fulfillment_min`, `p50`, `p95` | over orders delivered in the window: `delivered_tick − created_tick` |
| `sla_breach_rate` | late deliveries / deliveries |
| `sla_breach_rate_projected` | (late deliveries + open orders already past their deadline) / (deliveries + open orders) — the headline number |
| `throughput_per_hour` | deliveries per simulated hour of the window |
| `robot_utilization` | productive robot-ticks (moving/picking/delivering/unloading) / operational robot-ticks |
| `robot_availability` | operational robots / total |
| `distance_total`, `energy_total` | cells travelled, battery percentage points consumed (fleet totals) |
| `congestion_index` | mean over ticks of `Σ_zones max(0, robots_in_zone − capacity)` |
| `wait_ticks_per_robot_hour` | blocked ticks per operational robot-hour |
| `replans`, `failures`, `charging_sessions`, `inventory_units`, `avg_lateness_min`, order counts | as named |

`compute_kpis(world, since_tick)` evaluates a window (used for decision horizons); `kpi_delta` compares two headline sets.

## Event vocabulary (`nexus/events/types.py`)

| Group | Types |
|---|---|
| Orders | `ORDER_CREATED`, `ORDER_ASSIGNED`, `ORDER_STARTED`, `ORDER_DELIVERED`, `ORDER_CANCELLED`, `ORDER_REPRIORITIZED` |
| Tasks | `TASK_CREATED`, `TASK_REASSIGNED`, `TASK_CANCELLED`, `TASK_COMPLETED` |
| Robots | `ROBOT_PATH_SET`*, `ROBOT_MOVED`*, `ROBOT_WAITING`*, `ROBOT_STATUS_CHANGED`, `WAYPOINT_REACHED`, `BATTERY_UPDATED`*, `ITEM_PICKED`, `ITEM_DROPPED`, `BATTERY_LOW`, `CHARGING_STARTED`, `CHARGING_COMPLETED`, `ROBOT_FAILURE`, `ROBOT_RECOVERED`, `ROBOT_ADDED`, `ROBOT_REMOVED` |
| Workers | `WORKER_DELAY`, `WORKER_STATUS_CHANGED` |
| Infrastructure | `AISLE_BLOCKED`, `AISLE_CLEARED`, `ZONE_CLOSED`, `ZONE_OPENED`, `DOCK_CLOSED`, `DOCK_OPENED`, `CHARGER_DISABLED`, `CHARGER_ENABLED` |
| Inventory | `INVENTORY_MOVED`, `INVENTORY_RESTOCKED`, `SHIPMENT_DEPARTED` |
| Policy / demand | `DEMAND_CHANGED`, `CONFIG_CHANGED` |
| Plans | `PLAN_PROPOSED`, `PLAN_APPROVED`, `PLAN_REJECTED`, `PLAN_EXECUTED` |
| System | `TICK`*, `SNAPSHOT_TAKEN` |

\* ephemeral — streamed to observers and kept in the ring buffer, not persisted; the engine regenerates them on replay.

Each `Event` carries `type, tick, entity_id, payload, origin (engine | user | agent | scenario), seq, id, key
(idempotency), cause (plan or event id), ephemeral`. The `EventStore` assigns `seq`/`id`, rejects duplicate keys,
keeps a 5,000-event ring buffer, and forwards every event to registered sinks (persistence, metrics).

## Replay model (`nexus/events/replay.py`)

Only **external** events (origin ≠ `engine`) need to be stored to reproduce a run: everything the engine produces is a
deterministic function of the world and the seed. `replay(snapshot, external_events, engine_factory, until_tick)`
restores the world, re-injects each external event at its original tick before that tick's step, and steps the engine;
`verify_replay` asserts that the replayed digest equals the live digest. This is the lockstep-simulation model — small
logs, exact reconstruction, and a built-in integrity check.
