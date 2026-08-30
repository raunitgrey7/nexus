"""Plan executor: translates validated actions into events (and strategy parameter changes).

The same executor runs against a forked simulation world (to evaluate a plan) and against the live
world (after approval). All events carry ``origin="agent"``, ``cause=<plan id>`` and an idempotency key,
so executing the same plan twice is a no-op and every change is attributable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nexus.api.schemas import ActionModel, PlanModel
from nexus.core.logging import get_logger
from nexus.events.types import Event, EventType
from nexus.simulation.strategies import make_strategy
from nexus.twin.entities import Cell, OrderPriority, OrderStatus, RobotStatus, TaskStatus

if TYPE_CHECKING:
    from nexus.simulation.engine import SimulationEngine

log = get_logger("nexus.agents.executor")
PRIORITY_ORDER = ["LOW", "NORMAL", "HIGH", "CRITICAL"]


def _optimizer(engine: SimulationEngine) -> Any:
    from nexus.optimization.engine import OptimizationEngine  # local import: package built separately

    return OptimizationEngine(engine.world, engine.pathfinder)


class PlanExecutor:
    def __init__(self, engine: SimulationEngine) -> None:
        self.engine = engine
        self.applied: list[Event] = []
        self.allocations_evaluated = 0

    # ---- entry point ---------------------------------------------------------------------------
    def execute(self, plan: PlanModel, origin: str = "agent") -> list[Event]:
        self.applied = []
        for i, action in enumerate(plan.actions):
            try:
                self._apply(action, plan.id, i, origin)
            except Exception as exc:
                log.warning("executor.action_failed", plan=plan.id, action=action.type, error=str(exc)[:200])
        return self.applied

    def _emit(
        self,
        type_: EventType,
        entity_id: str | None,
        payload: dict[str, Any],
        plan_id: str,
        idx: int,
        sub: int,
        origin: str,
    ) -> Event | None:
        ev = self.engine.inject(
            type_,
            entity_id,
            payload,
            origin=origin,
            key=f"{plan_id}:{idx}:{sub}:{type_.value}",
            cause=plan_id,
        )
        if ev is not None:
            self.applied.append(ev)
        return ev

    # ---- actions -------------------------------------------------------------------------------
    def _apply(self, a: ActionModel, plan_id: str, idx: int, origin: str) -> None:
        world = self.engine.world
        strategy = self.engine.strategy
        p = a.params
        t = a.type
        tick = world.clock.tick
        if t == "NOOP":
            return
        if t == "REASSIGN_TASKS":
            self._reassign(p, plan_id, idx, origin)
        elif t == "REPRIORITIZE_ORDERS":
            boost = int(p.get("boost_minutes", 3)) * 60 // world.clock.tick_seconds
            min_pri = PRIORITY_ORDER.index(p["priority_at_least"]) if "priority_at_least" in p else None
            zones = set(p.get("zones", []))
            n = 0
            for order in sorted(world.open_orders(), key=lambda o: o.id):
                if min_pri is not None and int(order.priority) < min_pri:
                    continue
                if zones and not any(
                    world.shelves[ln.shelf_id].zone_id in zones
                    for ln in order.lines
                    if ln.shelf_id in world.shelves
                ):
                    continue
                payload: dict[str, Any] = {
                    "order_id": order.id,
                    "deadline_tick": max(tick + 30, order.deadline_tick - boost),
                }
                if zones and min_pri is None and order.priority < OrderPriority.HIGH:
                    payload["priority"] = int(OrderPriority.HIGH)
                self._emit(EventType.ORDER_REPRIORITIZED, order.id, payload, plan_id, idx, n, origin)
                n += 1
                if n >= 200:
                    break
        elif t == "SEND_TO_CHARGE":
            pending = getattr(strategy, "pending_charge", None)
            for k, rid in enumerate(p["robot_ids"]):
                robot = world.robots.get(rid)
                if (
                    robot is None
                    or not robot.status.operational
                    or robot.status in (RobotStatus.CHARGING, RobotStatus.TO_CHARGER)
                ):
                    continue
                if robot.task_id and p.get("after_current_task", True):
                    if pending is not None:
                        pending.add(rid)
                    continue
                charger = self.engine._pick_charger(robot)
                if charger is None:
                    continue
                if robot.task_id:
                    self._emit(
                        EventType.TASK_CANCELLED,
                        rid,
                        {"task_id": robot.task_id, "reason": "charging_plan"},
                        plan_id,
                        idx,
                        100 + k,
                        origin,
                    )
                self._emit(
                    EventType.BATTERY_LOW,
                    rid,
                    {"battery": round(robot.battery, 2), "charger_id": charger.id, "planned": True},
                    plan_id,
                    idx,
                    200 + k,
                    origin,
                )
                self._emit(
                    EventType.ROBOT_STATUS_CHANGED,
                    rid,
                    {"status": RobotStatus.TO_CHARGER.value, "charger_id": charger.id},
                    plan_id,
                    idx,
                    300 + k,
                    origin,
                )
        elif t in ("REROUTE_AVOID_ZONE", "PREFER_CORRIDOR", "SET_ZONE_CAPACITY"):
            policy = getattr(strategy, "routing_policy", None)
            if policy is None:
                from nexus.optimization.routing import RoutingPolicy

                policy = RoutingPolicy()
                if hasattr(strategy, "routing_policy"):
                    strategy.routing_policy = policy  # type: ignore[attr-defined]
            until = tick + int(p.get("duration_min", 30)) * 60 // world.clock.tick_seconds
            if t == "REROUTE_AVOID_ZONE":
                for z in p["zones"]:
                    policy.avoid_zones[z] = float(p.get("penalty", 4.0))
                    policy.until_tick[z] = until
            elif t == "PREFER_CORRIDOR":
                for c in p["corridors"]:
                    policy.prefer_corridors[c] = float(p.get("bonus", 0.4))
                    policy.until_tick[c] = until
            else:
                policy.zone_capacity_override.update({z: int(c) for z, c in p["zones"].items()})
                self._emit(
                    EventType.CONFIG_CHANGED, None, {"capacities": p["zones"]}, plan_id, idx, 0, origin
                )
            if hasattr(strategy, "routing_policy"):
                strategy.routing_policy = policy  # type: ignore[attr-defined]
        elif t == "REPOSITION_INVENTORY":
            events = _optimizer(self.engine).reposition_inventory_events(
                p["from_zone"], p["to_zone"], int(p.get("skus", 6)), int(p.get("units", 40))
            )
            for k, (etype, entity, payload) in enumerate(events):
                self._emit(etype, entity, payload, plan_id, idx, k, origin)
        elif t == "SET_BATCHING":
            n = int(p["orders_per_trip"])
            self._emit(EventType.CONFIG_CHANGED, None, {"batch_max_orders": n}, plan_id, idx, 0, origin)
            if hasattr(strategy, "batch_max"):
                strategy.batch_max = n  # type: ignore[attr-defined]
        elif t == "CLOSE_ZONE":
            self._emit(EventType.ZONE_CLOSED, p["zone_id"], {"reason": "plan"}, plan_id, idx, 0, origin)
        elif t == "OPEN_ZONE":
            self._emit(EventType.ZONE_OPENED, p["zone_id"], {}, plan_id, idx, 0, origin)
        elif t == "ADD_ROBOTS":
            bay = world.zones.get("CHG")
            cells = (
                [Cell(x, y) for y in range(bay.y0, bay.y1 + 1) for x in range(bay.x0 + 1, bay.x1 + 1)]
                if bay
                else [r.cell for r in world.robots.values()]
            )
            n_existing = len(world.robots)
            for k in range(int(p["count"])):
                rid = f"R{n_existing + k + 1:02d}"
                while rid in world.robots:
                    rid = rid + "x"
                cell = cells[(n_existing + k) % len(cells)]
                self._emit(
                    EventType.ROBOT_ADDED,
                    rid,
                    {
                        "robot": {
                            "id": rid,
                            "cell": list(cell),
                            "zone_id": world.zone_at(cell) or "CHG",
                            "battery": 100.0,
                        }
                    },
                    plan_id,
                    idx,
                    k,
                    origin,
                )
        elif t == "REMOVE_ROBOTS":
            for k, rid in enumerate(p["robot_ids"]):
                self._emit(EventType.ROBOT_REMOVED, rid, {"reason": "plan"}, plan_id, idx, k, origin)
        elif t == "DISPATCH_WORKER":
            dock = world.docks[p["dock_id"]]
            self._emit(
                EventType.WORKER_STATUS_CHANGED,
                p["worker_id"],
                {
                    "status": "available",
                    "cell": [dock.cell.x, min(dock.cell.y + 1, world.grid.height - 1)],
                    "zone_id": dock.zone_id,
                },
                plan_id,
                idx,
                0,
                origin,
            )
        elif t == "CANCEL_TASKS":
            for k, tid in enumerate(p["task_ids"]):
                task = world.tasks.get(tid)
                if task is not None and task.status == TaskStatus.ACTIVE:
                    self._emit(
                        EventType.TASK_CANCELLED,
                        task.robot_id,
                        {"task_id": tid, "reason": "plan"},
                        plan_id,
                        idx,
                        k,
                        origin,
                    )
        elif t == "SET_STRATEGY":
            name = p["name"]
            if getattr(strategy, "name", None) != name:
                self.engine.strategy = make_strategy(name)
                self._emit(EventType.CONFIG_CHANGED, None, {"strategy": name}, plan_id, idx, 0, origin)

    def _reassign(self, p: dict[str, Any], plan_id: str, idx: int, origin: str) -> None:
        world = self.engine.world
        from_robots = set(p.get("from_robots", []))
        zones = set(p.get("zones", []))
        to_robots = [
            r for r in p.get("to_robots", []) if r in world.robots and world.robots[r].status.operational
        ]
        max_tasks = int(p.get("max_tasks", 12))
        # 1) release tasks held by robots we are taking work from (failed robots already released theirs)
        n = 0
        for task in sorted(world.active_tasks(), key=lambda t: t.id):
            if task.robot_id in from_robots and world.robots[task.robot_id].status.operational:
                self._emit(
                    EventType.TASK_CANCELLED,
                    task.robot_id,
                    {"task_id": task.id, "reason": "reassignment"},
                    plan_id,
                    idx,
                    n,
                    origin,
                )
                n += 1
        # 2) free the helper robots so the optimizer can give them the released work
        for rid in to_robots:
            robot = world.robots[rid]
            if robot.task_id and robot.status in (RobotStatus.MOVING, RobotStatus.DELIVERING):
                held = world.tasks.get(robot.task_id)
                if held is not None and held.leg == 0:  # nothing picked yet: cheap to re-plan
                    self._emit(
                        EventType.TASK_CANCELLED,
                        rid,
                        {"task_id": held.id, "reason": "reassignment"},
                        plan_id,
                        idx,
                        n,
                        origin,
                    )
                    n += 1
        # 3) re-plan pending orders (optionally focused on zones) for the helper robots
        pending = list(world.pending_orders())
        if zones:
            focused = [
                o
                for o in pending
                if any(
                    world.shelves[ln.shelf_id].zone_id in zones
                    for ln in o.lines
                    if ln.shelf_id in world.shelves
                )
            ]
            others = [o for o in pending if o not in focused]
            pending = focused + others
        pending.sort(key=lambda o: (o.deadline_tick, o.id))
        pending = pending[:max_tasks]
        helpers = [world.robots[r] for r in to_robots if world.robots[r].available] or None
        if not pending:
            return
        result = _optimizer(self.engine).plan_assignments(
            robots=helpers, orders=pending, origin=f"plan:{plan_id}"
        )
        self.allocations_evaluated += int(getattr(result.result, "evaluated", 0))
        for k, task in enumerate(result.tasks):
            if (
                world.orders.get(task.order_ids[0]) is None
                or world.orders[task.order_ids[0]].status != OrderStatus.PENDING
            ):
                continue
            self._emit(
                EventType.TASK_CREATED,
                task.robot_id,
                {"task": task.to_dict()},
                plan_id,
                idx,
                1000 + k,
                origin,
            )
