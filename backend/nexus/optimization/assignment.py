"""Robot ↔ batch assignment: cost model and three solvers.

**Cost model.** For every (robot *r*, batch *b*) pair we estimate the trip:

* exact walking distance from the robot to the nearest pick of the batch (BFS distance field from
  the robot's cell — one BFS per robot, reused for every batch — never an A* per pair),
* nearest-neighbour tour over the remaining picks and the closest open dock (Manhattan × 1.25 detour),
* predicted finish tick ⇒ predicted lateness per order vs its deadline,
* battery need (with the engine's reserve factor) — infeasible if the robot cannot make it,
* congestion of the zones the trip touches (``max(0, occupancy − capacity) / capacity``),
* the active routing policy sampled at the pick cells.

::

    cost(r, b) = w_delivery · trip_min
               + w_late · Σ_orders max(0, finish − deadline)_min · w_priority(order)
               + w_battery · battery_need
               + w_congestion · Σ_zones excess(zone)
               + w_routing · Σ_picks policy_penalty(cell)

**CP-SAT model** (OR-Tools)::

    x[r,b] ∈ {0,1}                       assign batch b to robot r
    Σ_r x[r,b] ≤ 1        ∀ b            a batch goes to at most one robot
    Σ_b x[r,b] ≤ 1        ∀ r            a robot takes at most one batch per planning round
    maximise Σ x[r,b] · (REWARD − cost[r,b])

with ``REWARD > min(R,B) · max cost`` so that the solver first maximises the number of assigned
batches and only then minimises cost (lexicographic by construction). The CP-SAT solver runs with a
single worker and a fixed seed so that the whole simulation stays deterministic.

Fallbacks: the Hungarian algorithm (scipy) on the padded matrix, then a greedy cheapest-edge pass.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from nexus.optimization.constraints import battery_requirement
from nexus.optimization.objective import DEFAULT_WEIGHTS, ObjectiveWeights
from nexus.optimization.scheduling import priority_weight
from nexus.simulation.pathfinding import Pathfinder
from nexus.simulation.tasks import DETOUR_FACTOR
from nexus.twin.entities import Cell, Order, Robot
from nexus.twin.world import WorldState

INF = float("inf")
CostFn = Callable[[int], float]


@dataclass(slots=True)
class PairEstimate:
    cells: int
    picks: int
    finish_tick: int
    late_min: float
    battery_need: float
    zones: tuple[str, ...]
    dock_id: str | None
    cost: float
    breakdown: dict[str, float]


@dataclass
class AssignmentProblem:
    robot_ids: list[str]
    batches: list[list[str]]  # order ids per batch
    cost: list[list[float]]  # robot × batch; INF = infeasible
    estimates: dict[tuple[int, int], PairEstimate] = field(default_factory=dict)
    weights: ObjectiveWeights = field(default_factory=lambda: DEFAULT_WEIGHTS)
    build_ms: float = 0.0

    @property
    def n_robots(self) -> int:
        return len(self.robot_ids)

    @property
    def n_batches(self) -> int:
        return len(self.batches)

    def feasible(self, r: int, b: int) -> bool:
        return self.cost[r][b] < INF

    @property
    def evaluated(self) -> int:
        """Number of feasible (robot, batch) alternatives considered."""
        return sum(1 for row in self.cost for c in row if c < INF)

    @property
    def max_cost(self) -> float:
        best = 0.0
        for row in self.cost:
            for c in row:
                if c < INF and c > best:
                    best = c
        return best

    @property
    def unassigned_penalty(self) -> float:
        """Penalty for each batch left unassigned; larger than any feasible cost so that solvers
        prefer more assignments over cheaper-but-fewer ones."""
        return min(self.n_robots, self.n_batches) * self.max_cost + 1.0

    def objective(self, pairs: list[tuple[str, int]]) -> float:
        """Σ cost of the chosen pairs + penalty × unassigned slots (lower is better)."""
        index = {rid: i for i, rid in enumerate(self.robot_ids)}
        total = 0.0
        for rid, b in pairs:
            c = self.cost[index[rid]][b]
            total += c if c < INF else self.unassigned_penalty
        slots = min(self.n_robots, self.n_batches)
        return round(total + self.unassigned_penalty * max(0, slots - len(pairs)), 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "robots": list(self.robot_ids),
            "batches": [list(b) for b in self.batches],
            "evaluated": self.evaluated,
            "build_ms": round(self.build_ms, 2),
        }


@dataclass(slots=True)
class AssignmentResult:
    pairs: list[tuple[str, int]]
    method: str
    solve_ms: float
    objective: float
    evaluated: int
    assigned: int = 0
    status: str = "ok"

    def to_dict(self) -> dict[str, Any]:
        return {
            "pairs": [[r, b] for r, b in self.pairs],
            "method": self.method,
            "solve_ms": round(self.solve_ms, 2),
            "objective": self.objective,
            "evaluated": self.evaluated,
            "assigned": self.assigned,
            "status": self.status,
        }


def _pick_cells(world: WorldState, orders: list[Order]) -> list[tuple[Cell, str]]:
    picks: list[tuple[Cell, str]] = []
    for order in orders:
        for line in order.lines:
            if line.picked:
                continue
            shelf = world.shelves.get(line.shelf_id)
            if shelf is not None:
                picks.append((shelf.access_cell, shelf.zone_id))
    return picks


def _feasible(pairs: list[tuple[str, int]], problem: AssignmentProblem) -> bool:
    robots = [r for r, _ in pairs]
    batches = [b for _, b in pairs]
    return len(set(robots)) == len(robots) and len(set(batches)) == len(batches)


def build_problem(
    world: WorldState,
    robots: list[Robot],
    batches: list[list[Order]],
    pathfinder: Pathfinder,
    weights: ObjectiveWeights | None = None,
    routing_cost_fn: CostFn | None = None,
    boost: dict[str, float] | None = None,
) -> AssignmentProblem:
    """Build the cost matrix for ``robots`` × ``batches`` (see the module docstring)."""
    t0 = time.perf_counter()
    w = weights or DEFAULT_WEIGHTS
    cfg = world.config
    now = world.clock.tick
    ts = world.clock.tick_seconds
    width = world.grid.width
    speed = max(0.05, cfg.robot_speed)
    docks = [d for d in world.docks.values() if d.open]
    zone_excess: dict[str, float] = {}
    for zone in world.zones.values():
        occ = world.zone_occupancy.get(zone.id, 0)
        if zone.capacity > 0 and occ > zone.capacity:
            zone_excess[zone.id] = (occ - zone.capacity) / zone.capacity

    # Per batch, pre-compute the nearest-neighbour tour + dock leg for every possible first pick
    # (robot-independent), so each (robot, batch) pair only needs the exact robot → first-pick leg.
    batch_info: list[tuple[list[Order], list[tuple[Cell, str]], int, float, list[tuple[float, Any]]]] = []
    for batch in batches:
        picks = _pick_cells(world, batch)
        items = sum(o.items for o in batch)
        routing_pen = 0.0
        if routing_cost_fn is not None:
            routing_pen = sum(routing_cost_fn(c.y * width + c.x) for c, _ in picks)
        tours: list[tuple[float, Any]] = []
        for first_i in range(len(picks)):
            cur = picks[first_i][0]
            internal = 0.0
            remaining = [p[0] for i, p in enumerate(picks) if i != first_i]
            while remaining:
                j = min(range(len(remaining)), key=lambda k: (remaining[k].manhattan(cur), k))
                nxt = remaining.pop(j)
                internal += nxt.manhattan(cur) * DETOUR_FACTOR
                cur = nxt
            dock = (
                min(docks, key=lambda d: (d.cell.manhattan(cur) + 3 * len(d.queue), d.id)) if docks else None
            )
            if dock is not None:
                internal += dock.cell.manhattan(cur) * DETOUR_FACTOR
            tours.append((internal, dock))
        batch_info.append((batch, picks, items, routing_pen, tours))

    cost: list[list[float]] = []
    estimates: dict[tuple[int, int], PairEstimate] = {}
    for ri, robot in enumerate(robots):
        row = [INF] * len(batches)
        robot_ok = robot.status.operational and robot.available and robot.battery >= cfg.battery_low_threshold
        dist = pathfinder.bfs_distances(robot.cell) if robot_ok else None
        for bi, (orders, picks, items, routing_pen, tours) in enumerate(batch_info):
            if dist is None or not picks or not docks or items > robot.capacity:
                continue
            # exact distance to the best first pick (BFS), then the pre-computed tour from there
            first_i = -1
            best = INF
            for i, (cell, _) in enumerate(picks):
                d = dist[cell.y * width + cell.x]
                if d >= 0:
                    total = d + tours[i][0]
                    if total < best:
                        best, first_i = total, i
            if first_i < 0:
                continue
            dock = tours[first_i][1]
            cells_i = math.ceil(best)
            n_picks = len(picks)
            need = battery_requirement(world, cells_i, n_picks)
            if robot.battery < need:
                continue
            finish = now + int(cells_i / speed) + n_picks * cfg.pick_ticks + cfg.unload_ticks
            late_cost = 0.0
            late_min = 0.0
            for order in orders:
                late_ticks = max(0, finish - order.deadline_tick)
                if late_ticks:
                    minutes = late_ticks * ts / 60.0
                    late_min += minutes
                    late_cost += w.assign_lateness_per_min * minutes * priority_weight(order, w, boost)
            zones = tuple(sorted({z for _, z in picks} | {dock.zone_id}))
            cong = sum(zone_excess.get(z, 0.0) for z in zones)
            breakdown = {
                "delivery": round(w.assign_delivery_per_min * (finish - now) * ts / 60.0, 4),
                "lateness": round(late_cost, 4),
                "battery": round(w.assign_battery * need, 4),
                "congestion": round(w.assign_congestion * cong, 4),
                "routing": round(w.assign_routing * routing_pen, 4),
            }
            total = round(sum(breakdown.values()), 4)
            row[bi] = total
            estimates[(ri, bi)] = PairEstimate(
                cells_i, n_picks, finish, round(late_min, 3), round(need, 3), zones, dock.id, total, breakdown
            )
        cost.append(row)
    problem = AssignmentProblem(
        [r.id for r in robots], [[o.id for o in b] for b in batches], cost, estimates, w
    )
    problem.build_ms = (time.perf_counter() - t0) * 1000
    return problem


# ------------------------------------------------------------------------------------------------
# solvers
# ------------------------------------------------------------------------------------------------


def _empty(problem: AssignmentProblem, method: str, status: str = "empty") -> AssignmentResult:
    return AssignmentResult([], method, 0.0, problem.objective([]), problem.evaluated, 0, status)


def solve_greedy(problem: AssignmentProblem) -> AssignmentResult:
    """Cheapest feasible edge first; ties broken by indices for determinism."""
    t0 = time.perf_counter()
    edges = sorted((c, ri, bi) for ri, row in enumerate(problem.cost) for bi, c in enumerate(row) if c < INF)
    used_r: set[int] = set()
    used_b: set[int] = set()
    pairs: list[tuple[str, int]] = []
    for _cost, ri, bi in edges:
        if ri in used_r or bi in used_b:
            continue
        used_r.add(ri)
        used_b.add(bi)
        pairs.append((problem.robot_ids[ri], bi))
    pairs.sort()
    return AssignmentResult(
        pairs,
        "greedy",
        (time.perf_counter() - t0) * 1000,
        problem.objective(pairs),
        problem.evaluated,
        len(pairs),
    )


def solve_hungarian(problem: AssignmentProblem) -> AssignmentResult:
    """Kuhn-Munkres via ``scipy.optimize.linear_sum_assignment`` on the rectangular matrix."""
    import numpy as np
    from scipy.optimize import linear_sum_assignment

    t0 = time.perf_counter()
    if problem.evaluated == 0:
        return _empty(problem, "hungarian")
    big = problem.unassigned_penalty * 4.0
    matrix = np.array([[c if c < INF else big for c in row] for row in problem.cost], dtype=float)
    rows, cols = linear_sum_assignment(matrix)
    pairs = sorted(
        (problem.robot_ids[int(r)], int(b))
        for r, b in zip(rows, cols, strict=True)
        if problem.cost[int(r)][int(b)] < INF
    )
    return AssignmentResult(
        pairs,
        "hungarian",
        (time.perf_counter() - t0) * 1000,
        problem.objective(pairs),
        problem.evaluated,
        len(pairs),
    )


def solve_cpsat(problem: AssignmentProblem, time_limit_s: float = 0.2) -> AssignmentResult:
    """OR-Tools CP-SAT formulation (single worker + fixed seed ⇒ deterministic)."""
    from ortools.sat.python import cp_model

    t0 = time.perf_counter()
    if problem.evaluated == 0:
        return _empty(problem, "cpsat")
    scale = 1000.0
    max_int = round(problem.max_cost * scale)
    slots = min(problem.n_robots, problem.n_batches)
    reward = slots * max_int + int(scale)
    model = cp_model.CpModel()
    x: dict[tuple[int, int], Any] = {}
    by_batch: dict[int, list[Any]] = {}
    by_robot: dict[int, list[Any]] = {}
    for ri, row in enumerate(problem.cost):
        for bi, c in enumerate(row):
            if c < INF:
                var = model.new_bool_var(f"x_{ri}_{bi}")
                x[(ri, bi)] = var
                by_batch.setdefault(bi, []).append(var)
                by_robot.setdefault(ri, []).append(var)
    for vars_b in by_batch.values():
        model.add(sum(vars_b) <= 1)
    for vars_r in by_robot.values():
        model.add(sum(vars_r) <= 1)
    model.maximize(sum(v * (reward - round(problem.cost[ri][bi] * scale)) for (ri, bi), v in x.items()))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(0.01, time_limit_s)
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 7
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise RuntimeError(f"CP-SAT returned {solver.status_name(status)}")
    pairs = sorted((problem.robot_ids[ri], bi) for (ri, bi), v in x.items() if solver.value(v) == 1)
    label = "cpsat" if status == cp_model.OPTIMAL else "cpsat-feasible"
    return AssignmentResult(
        pairs,
        label,
        (time.perf_counter() - t0) * 1000,
        problem.objective(pairs),
        problem.evaluated,
        len(pairs),
    )


def solve(problem: AssignmentProblem, method: str = "auto", time_limit_s: float = 0.2) -> AssignmentResult:
    """Dispatch to a solver. ``auto`` = CP-SAT → Hungarian → greedy, with a greedy fast path when one
    side has a single element (greedy is optimal there)."""
    if problem.evaluated == 0:
        return _empty(problem, "none")
    if method == "greedy":
        return solve_greedy(problem)
    if method == "hungarian":
        return solve_hungarian(problem)
    if method == "cpsat":
        return solve_cpsat(problem, time_limit_s)
    if method == "ga":
        from nexus.optimization.genetic import GeneticAllocator

        return GeneticAllocator(problem).solve()
    if method != "auto":
        raise ValueError(f"unknown assignment method {method!r}")
    if min(problem.n_robots, problem.n_batches) == 1:
        result = solve_greedy(problem)
        result.method = "greedy-trivial"
        return result
    try:
        return solve_cpsat(problem, time_limit_s)
    except Exception:  # ortools missing, timeout without solution, …
        try:
            return solve_hungarian(problem)
        except Exception:
            return solve_greedy(problem)
