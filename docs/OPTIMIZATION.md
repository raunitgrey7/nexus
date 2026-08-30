# Optimization

`nexus/optimization` is where NEXUS stops being an AI wrapper and becomes operations research: a shared multi-objective
score, feasibility constraints, order batching, priority-weighted deadline sequencing, a robot↔batch assignment solved
with OR-Tools CP-SAT (Hungarian and greedy fallbacks, plus a memetic genetic allocator), congestion-aware routing
policies, and the `optimized` scheduling strategy that ties them together. Everything is deterministic (single-worker
CP-SAT with a fixed seed, seeded GA) so optimized simulations fork and replay exactly.

## The objective (`objective.py`)

One weighted cost scores every plan, what-if run and benchmark result, so "better" means the same thing everywhere:

```
score(K) =  w_lateness      · SLA_breach_projected(K)          # dominant: 1 percentage point = 1.0
          + w_delivery_time · avg_fulfillment_min(K)
          + w_tail          · p95_fulfillment_min(K)
          + w_congestion    · congestion_index(K)
          + w_distance      · distance_total(K) / delivered(K)  # cells per delivered order
          + w_energy        · energy_total(K)   / delivered(K)  # battery % per delivered order
          + w_backlog       · orders_pending(K)
```

Defaults (`ObjectiveWeights`): lateness 100, delivery_time 1, tail 0.25, congestion 2, distance 0.005, energy 0.1,
backlog 0.02. Lower is better; a plan only wins on distance/energy when it is not paying for it in lateness.
`score_breakdown` returns each term (used in explanations), `compare_scores` the per-term delta. The same object carries
the tactical per-pair weights used inside the assignment cost matrix (`assign_delivery_per_min` 1, `assign_lateness_per_min`
4, `assign_battery` 0.2, `assign_congestion` 3, `assign_routing` 1) so the solver and the scorer pull in the same direction.

## Constraints (`constraints.py`)

`assignment_feasible(world, robot, orders, batch_max)` answers "can this robot serve these orders as one trip right now?":
robot operational and available, battery above the low threshold, batch size ≤ `batch_max`, every order pending and
sourceable (`resolve_lines` re-targets lines to shelves with stock in open zones), items ≤ robot capacity, an open dock
exists, and

```
battery ≥ reserve_factor · (cells · drain_move + picks · pick_ticks · drain_action) + ½ · low_threshold
```

`validate_tasks` checks a set of tasks structurally before emission (no robot or order twice, walkable waypoints, open
delivery dock, capacity).

## Sequencing: priority-weighted EDF (`scheduling.py`)

```
key(order) = now + slack / w(priority)         slack = deadline − now (ticks)
w: LOW 0.6 · NORMAL 1.0 · HIGH 1.8 · CRITICAL 3.0   (× ObjectiveWeights.priority × per-order boost)
```

A CRITICAL order with 9 minutes of slack sorts like a NORMAL order with 3 minutes; overdue orders are multiplied instead of
divided so high-priority overdue orders sort first of all. `REPRIORITIZE_ORDERS` plans feed the per-order boosts.

## Batching (`batching.py`)

Greedy and deterministic over the urgency sequence: the most urgent unbatched order seeds a batch; candidates join in
urgency order while items fit the robot capacity and the batch stays below `batch_max`; every zone a candidate touches
must be within `max_hops` (1.0 ≈ across one corridor) of a zone the batch already touches; HIGH seeds accept only
same-zone companions and CRITICAL seeds are capped at two orders. Batching is the single biggest capacity lever in the
platform — it roughly doubles the capacity of the small layout (`docs/BENCHMARKS.md`).

## Assignment (`assignment.py`)

**Cost model.** For every (robot *r*, batch *b*): the exact walking distance from the robot to the best first pick (one
BFS distance field per robot, reused for every batch — never an A* per pair), a pre-computed nearest-neighbour tour over
the remaining picks and the closest open dock (Manhattan × 1.25 detour), the predicted finish tick and lateness per order,
the battery need (infeasible if the robot cannot make it), the congestion of the zones touched and the routing-policy
penalty sampled at the pick cells:

```
cost(r, b) = w_delivery · trip_min
           + w_late · Σ_orders max(0, finish − deadline)_min · w_priority(order)
           + w_battery · battery_need
           + w_congestion · Σ_zones max(0, occupancy − capacity) / capacity
           + w_routing · Σ_picks policy_penalty(cell)
```

**CP-SAT model** (OR-Tools):

```
x[r,b] ∈ {0,1}                       assign batch b to robot r
Σ_r x[r,b] ≤ 1        ∀ b            a batch goes to at most one robot
Σ_b x[r,b] ≤ 1        ∀ r            a robot takes at most one batch per planning round
maximise Σ x[r,b] · (REWARD − cost[r,b])      REWARD > min(R,B) · max cost
```

The reward makes the solver first maximise the number of assigned batches and only then minimise cost (lexicographic by
construction); costs are scaled ×1000 to integers; `num_search_workers = 1`, `random_seed = 7`, default time limit 0.2 s.

**Fallbacks.** `solve(problem, method="auto")`: a greedy fast path when one side has a single element (provably
optimal), else CP-SAT → Hungarian (`scipy.optimize.linear_sum_assignment` on the padded matrix) → greedy cheapest-edge.
`AssignmentResult` records `method, solve_ms, objective, evaluated` (the number of feasible robot–batch alternatives —
the "evaluated N allocations" in explanations), `assigned, status`.

## Genetic allocator (`genetic.py`)

A memetic GA doubling as a candidate generator: one gene per batch (robot index or −1), a repair step keeps every
chromosome feasible, the population is seeded with the greedy allocation, the elite is polished with pairwise
swap/reassign local search each generation, tournament selection, uniform crossover, swap/reassign mutation.
Fitness is `AssignmentProblem.objective`; `top_k(k)` returns the best distinct allocations of the final population.
Deterministic for a given seed (`population=40, generations=60` by default).

## Routing policies (`routing.py`)

A `RoutingPolicy` turns intent ("avoid Zone C for 30 minutes", "prefer corridor C4") and live state into a per-cell extra
cost consumed by A*:

```
extra(cell) = penalty(zone) − min(0.9, bonus(zone)) + k · max(0, occupancy − capacity) / capacity      (k = 1.5)
```

Penalties/bonuses expire at `until_tick`; the congestion term applies only to traffic zones (storage and corridors);
`zone_capacity_override` lets a plan tighten a soft capacity; `cost_fn(world)` returns `None` when nothing applies so the
pathfinder can keep using its cache. The policy is plain, picklable data, because strategies carrying it are simulated
inside forked worlds and worker processes.

## The `optimized` strategy (`strategy.py`)

Every `max(assign_every=5, robots // 10)` ticks — or immediately after a structural event (`ROBOT_FAILURE`,
`ROBOT_RECOVERED`, `ZONE_*`, `DOCK_*`, `TASK_CANCELLED`, `ROBOT_ADDED`) — the strategy: expires routing policies →
sends idle robots below the low threshold + margin (or below 55 % when nothing is pending) to charge → sequences pending
orders (weighted EDF) → batches them (`batch_max=3`) → builds the cost matrix → solves (`method="auto"`) → emits
`TASK_CREATED` for every pair. `route()` uses A* with the policy's cost function. The agents steer it at runtime through
`routing_policy`, `batch_max`, `pending_charge` (charge after the current task) and `priority_boost`.

`OptimizationEngine` is the façade the strategy, the plan executor and the what-if engine share:
`plan_assignments(...) -> PlanResult`, `reassign_after_failure(robot, to_robots)`,
`reposition_inventory_events(from_zone, to_zone, skus, units)` (moves the hottest SKUs of a zone into free slots of
another), `charging_candidates(margin)`, `explain_last()`.

**Ablation.** `optimized_greedy` runs the same machinery with the greedy solver and no batching, isolating the value of
CP-SAT + batching. `python -m nexus.optimization.quick_compare` compares strategies on a fresh world.

## Performance notes

Cost matrices are built from cached BFS fields; CP-SAT is bounded at 0.2 s per round and skipped when nothing changed
(`_failed_signature`). Indicative numbers from development (old calibration, seed 42, uncontended): small 120 min
baseline 3.04 % → optimized 0.00 % projected SLA breach and 2.91 → 1.76 min average fulfillment; medium 60 min
0.42 % → 0.14 %; large 30 min 2.34 % → 1.09 %. Simulation speed with the optimizer is ≈2.3k / 0.6k / 0.2k ticks/s for
small / medium / large. These are illustrative — the authoritative numbers, produced by identical worlds with an incident
schedule, are in `docs/BENCHMARKS.md`.
