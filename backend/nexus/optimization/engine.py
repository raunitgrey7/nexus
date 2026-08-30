"""Optimization engine façade.

Everything the rest of NEXUS needs from operations research goes through :class:`OptimizationEngine`:
assignment planning (batching → weighted EDF → cost matrix → CP-SAT), failure reassignment,
inventory repositioning proposals and pre-emptive charging candidates. The engine is *pure*: it
returns tasks / event payloads and never emits events itself — the caller (a strategy or the plan
executor) decides what becomes real.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from nexus.events.types import EventType
from nexus.optimization.assignment import AssignmentProblem, AssignmentResult, CostFn, build_problem, solve
from nexus.optimization.batching import batch_summary, build_batches
from nexus.optimization.constraints import battery_requirement
from nexus.optimization.objective import DEFAULT_WEIGHTS, ObjectiveWeights
from nexus.optimization.routing import RoutingPolicy
from nexus.optimization.scheduling import sequence_orders
from nexus.simulation.pathfinding import Pathfinder
from nexus.simulation.tasks import DETOUR_FACTOR, make_task, resolve_lines
from nexus.twin.entities import Order, Robot, RobotStatus, Task, TaskStatus, WaypointKind
from nexus.twin.world import WorldState


@dataclass
class PlanResult:
    tasks: list[Task]
    result: AssignmentResult
    batches: int
    unassigned_orders: list[str] = field(default_factory=list)
    problem: AssignmentProblem | None = None

    @property
    def assigned_orders(self) -> list[str]:
        return [oid for t in self.tasks for oid in t.order_ids]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tasks": [t.to_dict() for t in self.tasks],
            "result": self.result.to_dict(),
            "batches": self.batches,
            "unassigned_orders": list(self.unassigned_orders),
        }


class OptimizationEngine:
    def __init__(
        self,
        world: WorldState,
        pathfinder: Pathfinder | None = None,
        weights: ObjectiveWeights | None = None,
    ) -> None:
        self.world = world
        self.pathfinder = pathfinder or Pathfinder(world)
        self.weights = weights or DEFAULT_WEIGHTS
        self._last: dict[str, Any] = {}

    # ---- assignment ----------------------------------------------------------------------------
    def plan_assignments(
        self,
        robots: list[Robot] | list[str] | None = None,
        orders: list[Order] | list[str] | None = None,
        method: str = "auto",
        batch_max: int | None = None,
        time_limit_s: float = 0.2,
        routing_policy: RoutingPolicy | None = None,
        origin: str = "optimizer",
        max_batches_per_robot: int = 4,
        priority_boost: dict[str, float] | None = None,
    ) -> PlanResult:
        """Batch → sequence → cost matrix → solve → tasks. Never emits events."""
        world = self.world
        cfg = world.config
        t0 = time.perf_counter()
        robot_objs = self._robots(robots)
        robot_objs = [r for r in robot_objs if r.available and r.battery >= cfg.battery_low_threshold]
        order_objs = [o for o in self._orders(orders) if o.status.value == "pending"]
        order_objs = [o for o in order_objs if resolve_lines(world, o)]
        if not robot_objs or not order_objs:
            result = AssignmentResult([], "none", 0.0, 0.0, 0, 0, "nothing-to-plan")
            self._last = {
                "method": "none",
                "solve_ms": 0.0,
                "evaluated": 0,
                "objective": 0.0,
                "assigned": 0,
                "batches": 0,
            }
            return PlanResult([], result, 0, [o.id for o in order_objs])
        bmax = max(1, batch_max if batch_max is not None else cfg.batch_max_orders)
        sequenced = sequence_orders(world, order_objs, self.weights, priority_boost)
        cap = max_batches_per_robot * len(robot_objs) * bmax
        considered = sequenced[:cap]
        capacity = min(r.capacity for r in robot_objs)
        batches = build_batches(world, considered, bmax, capacity)
        cost_fn: CostFn | None = routing_policy.cost_fn(world) if routing_policy is not None else None
        problem = build_problem(
            world, robot_objs, batches, self.pathfinder, self.weights, cost_fn, priority_boost
        )
        result = solve(problem, method, time_limit_s)
        tasks: list[Task] = []
        assigned: set[str] = set()
        for rid, bi in result.pairs:
            robot = world.robots[rid]
            task = make_task(world, robot, batches[bi], origin=origin, check_battery=False)
            if task is None:
                continue
            tasks.append(task)
            assigned.update(task.order_ids)
        unassigned = [o.id for o in order_objs if o.id not in assigned]
        self._last = {
            "method": result.method,
            "solve_ms": round(result.solve_ms, 2),
            "build_ms": round(problem.build_ms, 2),
            "total_ms": round((time.perf_counter() - t0) * 1000, 2),
            "evaluated": result.evaluated,
            "objective": result.objective,
            "assigned": len(tasks),
            "robots": len(robot_objs),
            "orders": len(order_objs),
            "unassigned": len(unassigned),
            **batch_summary(batches),
        }
        return PlanResult(tasks, result, len(batches), unassigned, problem)

    def reassign_after_failure(
        self, failed_robot_id: str, to_robots: list[str] | None = None, batch_max: int | None = None
    ) -> PlanResult:
        """Re-plan the orders released by a robot failure, optionally restricted to ``to_robots``."""
        world = self.world
        failed = world.robots.get(failed_robot_id)
        candidates: list[Robot]
        if to_robots is not None:
            candidates = [world.robots[r] for r in to_robots if r in world.robots and r != failed_robot_id]
        else:
            candidates = [r for r in world.robots.values() if r.id != failed_robot_id]
        pending = world.pending_orders()
        if failed is not None and failed.status == RobotStatus.FAILED:
            # prefer the orders that were on the failed robot's cancelled task(s), then everything pending
            released = {
                oid
                for t in world.tasks.values()
                if t.robot_id == failed_robot_id and t.status == TaskStatus.CANCELLED
                for oid in t.order_ids
            }
            pending.sort(key=lambda o: (0 if o.id in released else 1, o.created_tick, o.id))
        plan = self.plan_assignments(
            candidates, pending, batch_max=batch_max, origin=f"reassign:{failed_robot_id}"
        )
        self._last["reason"] = f"failure of {failed_robot_id}"
        return plan

    # ---- inventory -----------------------------------------------------------------------------
    def reposition_inventory_events(
        self, from_zone: str, to_zone: str, skus: int = 6, units: int = 40, max_skus_per_shelf: int = 3
    ) -> list[tuple[EventType, str | None, dict[str, Any]]]:
        """Propose ``INVENTORY_MOVED`` payloads moving the hottest SKUs of ``from_zone`` into ``to_zone``."""
        world = self.world
        if from_zone == to_zone or from_zone not in world.zones or to_zone not in world.zones:
            return []
        src_shelves = [s for s in world.shelves.values() if s.zone_id == from_zone]
        dst_shelves = [s for s in world.shelves.values() if s.zone_id == to_zone]
        if not src_shelves or not dst_shelves:
            return []
        stock: dict[str, int] = {}
        for shelf in src_shelves:
            for sku, qty in shelf.inventory.items():
                if qty > 0:
                    stock[sku] = stock.get(sku, 0) + qty
        hottest = sorted(stock, key=lambda s: (-world.sku_popularity.get(s, 0.0), s))[:skus]
        if not hottest:
            return []
        per_sku = max(1, units // len(hottest))
        events: list[tuple[EventType, str | None, dict[str, Any]]] = []
        dst_load = {s.id: len(s.inventory) for s in dst_shelves}
        for sku in hottest:
            source = max(src_shelves, key=lambda s: (s.inventory.get(sku, 0), s.id))
            available = source.inventory.get(sku, 0)
            if available <= 0:
                continue
            qty = min(per_sku, available)
            holding = [s for s in dst_shelves if sku in s.inventory]
            if holding:
                target = min(holding, key=lambda s: (s.units, s.id))
            else:
                # prefer a free slot; otherwise re-slot onto the least loaded shelf (soft slot limit + 1)
                free = [s for s in dst_shelves if dst_load[s.id] < max_skus_per_shelf]
                if not free:
                    free = [s for s in dst_shelves if dst_load[s.id] <= max_skus_per_shelf]
                if not free:
                    continue
                target = min(free, key=lambda s: (dst_load[s.id], s.units, s.id))
                dst_load[target.id] += 1
            events.append(
                (
                    EventType.INVENTORY_MOVED,
                    None,
                    {"sku": sku, "from_shelf": source.id, "to_shelf": target.id, "qty": qty},
                )
            )
        return events

    # ---- charging ------------------------------------------------------------------------------
    def charging_candidates(self, extra_margin: float = 10.0) -> list[str]:
        """Robots that will dip below the low-battery threshold (+ margin) before finishing their task."""
        world = self.world
        cfg = world.config
        out: list[str] = []
        for rid in sorted(world.robots):
            robot = world.robots[rid]
            if not robot.status.operational or robot.status in (RobotStatus.CHARGING, RobotStatus.TO_CHARGER):
                continue
            task = world.tasks.get(robot.task_id or "")
            if task is None or task.status != TaskStatus.ACTIVE:
                if robot.battery < cfg.battery_low_threshold + extra_margin:
                    out.append(rid)
                continue
            cells = 0.0
            cur = robot.cell
            picks = 0
            for wp in task.remaining:
                cells += cur.manhattan(wp.cell) * DETOUR_FACTOR
                cur = wp.cell
                if wp.kind == WaypointKind.PICK:
                    picks += 1
            need = battery_requirement(world, int(cells), picks)
            if robot.battery - need < cfg.battery_low_threshold + extra_margin:
                out.append(rid)
        return out

    # ---- helpers -------------------------------------------------------------------------------
    def _robots(self, robots: list[Robot] | list[str] | None) -> list[Robot]:
        if robots is None:
            return sorted(self.world.available_robots(), key=lambda r: r.id)
        out: list[Robot] = []
        for r in robots:
            robot = self.world.robots.get(r) if isinstance(r, str) else r
            if robot is not None:
                out.append(robot)
        return sorted(out, key=lambda r: r.id)

    def _orders(self, orders: list[Order] | list[str] | None) -> list[Order]:
        if orders is None:
            return sorted(self.world.pending_orders(), key=lambda o: (o.created_tick, o.id))
        out: list[Order] = []
        for o in orders:
            order = self.world.orders.get(o) if isinstance(o, str) else o
            if order is not None:
                out.append(order)
        return out

    def explain_last(self) -> dict[str, Any]:
        return dict(self._last)
