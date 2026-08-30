"""The reducer: ``apply(world, event)`` is the only code path that mutates a world.

Handlers are small, total (they never raise on stale state — they log and skip), and free of I/O
and randomness. That is what makes replay and forks trustworthy.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nexus.events.types import Event, EventType
from nexus.twin.entities import (
    Cell,
    Order,
    OrderPriority,
    OrderStatus,
    Robot,
    RobotStatus,
    Task,
    TaskStatus,
    WorkerStatus,
)
from nexus.twin.world import WorldState

Handler = Callable[[WorldState, Event], None]
_HANDLERS: dict[EventType, Handler] = {}


def handles(type_: EventType) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        _HANDLERS[type_] = fn
        return fn

    return deco


def apply(world: WorldState, event: Event) -> None:
    handler = _HANDLERS.get(event.type)
    if handler is None:
        raise ValueError(f"no reducer for event type {event.type}")
    handler(world, event)
    world.version += 1


def _cell(v: Any) -> Cell:
    return Cell(int(v[0]), int(v[1]))


def _set_status(world: WorldState, robot: Robot, status: RobotStatus) -> None:
    robot.status = status


def _free_robot(world: WorldState, robot: Robot) -> None:
    robot.task_id = None
    robot.path = []
    robot.action_until_tick = 0
    robot.wait_ticks = 0
    robot.load = 0
    if robot.status.operational and robot.status not in (RobotStatus.CHARGING, RobotStatus.TO_CHARGER):
        robot.status = RobotStatus.IDLE


def _release_orders_of_task(world: WorldState, task: Task, tick: int) -> None:
    """Orders of a cancelled task go back to the pending pool; picked items are re-shelved."""
    for oid in task.order_ids:
        order = world.orders.get(oid)
        if order is None or not order.status.open:
            continue
        for line in order.lines:
            if line.picked:
                shelf = world.shelves.get(line.shelf_id)
                if shelf is not None:
                    shelf.inventory[line.sku] = shelf.inventory.get(line.sku, 0) + line.qty
                line.picked = False
        order.task_id = None
        order.robot_id = None
        order.started_tick = None
        world.mark_order_status(order, OrderStatus.PENDING)


# ------------------------------------------------------------------------------------------------
# orders
# ------------------------------------------------------------------------------------------------


@handles(EventType.ORDER_CREATED)
def _order_created(world: WorldState, ev: Event) -> None:
    order = Order.from_dict(ev.payload["order"])
    world.orders[order.id] = order
    world.mark_order_status(order, OrderStatus.PENDING)
    world.stats.orders_created += 1


@handles(EventType.ORDER_ASSIGNED)
def _order_assigned(world: WorldState, ev: Event) -> None:
    order = world.orders.get(ev.payload["order_id"])
    if order is None:
        return
    order.task_id = ev.payload.get("task_id")
    order.robot_id = ev.payload.get("robot_id")
    order.dock_id = ev.payload.get("dock_id")
    world.mark_order_status(order, OrderStatus.ASSIGNED)


@handles(EventType.ORDER_STARTED)
def _order_started(world: WorldState, ev: Event) -> None:
    order = world.orders.get(ev.payload["order_id"])
    if order is None:
        return
    order.started_tick = ev.tick
    world.mark_order_status(order, OrderStatus.IN_PROGRESS)


@handles(EventType.ORDER_DELIVERED)
def _order_delivered(world: WorldState, ev: Event) -> None:
    order = world.orders.get(ev.payload["order_id"])
    if order is None:
        return
    order.delivered_tick = ev.tick
    world.mark_order_status(order, OrderStatus.DELIVERED)
    world.stats.orders_delivered += 1
    world.stats.fulfillment_ticks_total += ev.tick - order.created_tick
    if ev.tick > order.deadline_tick:
        world.stats.orders_late += 1
        world.stats.lateness_ticks_total += ev.tick - order.deadline_tick
    dock = world.docks.get(ev.payload.get("dock_id") or "")
    if dock is not None:
        dock.delivered += 1
    robot = world.robots.get(ev.payload.get("robot_id") or "")
    if robot is not None:
        robot.orders_completed += 1
        robot.load = max(0, robot.load - order.items)


@handles(EventType.ORDER_CANCELLED)
def _order_cancelled(world: WorldState, ev: Event) -> None:
    order = world.orders.get(ev.payload["order_id"])
    if order is None or not order.status.open:
        return
    order.cancelled_tick = ev.tick
    world.stats.orders_cancelled += 1
    task = world.tasks.get(order.task_id or "")
    if task is not None and task.status in (TaskStatus.PLANNED, TaskStatus.ACTIVE):
        task.order_ids = [o for o in task.order_ids if o != order.id]
        task.waypoints = [w for w in task.waypoints if w.order_id != order.id or w.done]
        if task.leg >= len(task.waypoints) or not task.order_ids:
            task.status = TaskStatus.CANCELLED
            robot = world.robots.get(task.robot_id)
            if robot is not None:
                _free_robot(world, robot)
        else:
            robot = world.robots.get(task.robot_id)
            if robot is not None:
                robot.path = []
    for line in order.lines:
        if line.picked:
            shelf = world.shelves.get(line.shelf_id)
            if shelf is not None:
                shelf.inventory[line.sku] = shelf.inventory.get(line.sku, 0) + line.qty
    world.mark_order_status(order, OrderStatus.CANCELLED)


@handles(EventType.ORDER_REPRIORITIZED)
def _order_reprioritized(world: WorldState, ev: Event) -> None:
    order = world.orders.get(ev.payload["order_id"])
    if order is None:
        return
    if "priority" in ev.payload:
        order.priority = OrderPriority(int(ev.payload["priority"]))
    if "deadline_tick" in ev.payload:
        order.deadline_tick = int(ev.payload["deadline_tick"])


# ------------------------------------------------------------------------------------------------
# tasks
# ------------------------------------------------------------------------------------------------


@handles(EventType.TASK_CREATED)
def _task_created(world: WorldState, ev: Event) -> None:
    task = Task.from_dict(ev.payload["task"])
    robot = world.robots.get(task.robot_id)
    if robot is None:
        return
    world.tasks[task.id] = task
    task.status = TaskStatus.ACTIVE
    robot.task_id = task.id
    robot.path = []
    robot.wait_ticks = 0
    first = task.current
    if first is not None and first.kind.value == "deliver":
        robot.status = RobotStatus.DELIVERING
    elif first is not None and first.kind.value == "charge":
        robot.status = RobotStatus.TO_CHARGER
    else:
        robot.status = RobotStatus.MOVING
    dock_id = next((w.target_id for w in task.waypoints if w.kind.value == "deliver"), None)
    for oid in task.order_ids:
        order = world.orders.get(oid)
        if order is None:
            continue
        order.task_id = task.id
        order.robot_id = robot.id
        order.dock_id = dock_id
        world.mark_order_status(order, OrderStatus.ASSIGNED)


@handles(EventType.TASK_REASSIGNED)
def _task_reassigned(world: WorldState, ev: Event) -> None:
    task = world.tasks.get(ev.payload["task_id"])
    if task is None or task.status not in (TaskStatus.PLANNED, TaskStatus.ACTIVE):
        return
    old = world.robots.get(task.robot_id)
    new = world.robots.get(ev.payload["to_robot"])
    if new is None or not new.available:
        return
    if old is not None:
        # picked items travel with the old robot; re-shelve them so the new robot re-picks
        for oid in task.order_ids:
            order = world.orders.get(oid)
            if order is None:
                continue
            for line in order.lines:
                if line.picked:
                    shelf = world.shelves.get(line.shelf_id)
                    if shelf is not None:
                        shelf.inventory[line.sku] = shelf.inventory.get(line.sku, 0) + line.qty
                    line.picked = False
        _free_robot(world, old)
    for w in task.waypoints:
        if w.kind.value == "pick":
            w.done = False
    task.leg = 0
    task.robot_id = new.id
    task.origin = ev.payload.get("origin", task.origin)
    new.task_id = task.id
    new.path = []
    new.load = 0
    new.status = RobotStatus.MOVING
    for oid in task.order_ids:
        order = world.orders.get(oid)
        if order is not None:
            order.robot_id = new.id


@handles(EventType.TASK_CANCELLED)
def _task_cancelled(world: WorldState, ev: Event) -> None:
    task = world.tasks.get(ev.payload["task_id"])
    if task is None or task.status not in (TaskStatus.PLANNED, TaskStatus.ACTIVE):
        return
    task.status = TaskStatus.CANCELLED
    task.completed_tick = ev.tick
    _release_orders_of_task(world, task, ev.tick)
    robot = world.robots.get(task.robot_id)
    if robot is not None and robot.task_id == task.id:
        _free_robot(world, robot)


@handles(EventType.TASK_COMPLETED)
def _task_completed(world: WorldState, ev: Event) -> None:
    task = world.tasks.get(ev.payload["task_id"])
    if task is None:
        return
    task.status = TaskStatus.COMPLETED
    task.completed_tick = ev.tick
    robot = world.robots.get(task.robot_id)
    if robot is not None:
        robot.tasks_completed += 1
        _free_robot(world, robot)


# ------------------------------------------------------------------------------------------------
# robots
# ------------------------------------------------------------------------------------------------


@handles(EventType.ROBOT_PATH_SET)
def _robot_path_set(world: WorldState, ev: Event) -> None:
    robot = world.robots.get(ev.entity_id or "")
    if robot is None:
        return
    robot.path = [_cell(c) for c in ev.payload["path"]]
    robot.wait_ticks = 0
    if ev.payload.get("replan"):
        world.stats.replans_total += 1


@handles(EventType.ROBOT_MOVED)
def _robot_moved(world: WorldState, ev: Event) -> None:
    robot = world.robots.get(ev.entity_id or "")
    if robot is None:
        return
    to = _cell(ev.payload["to"])
    world.place_robot(robot, to)
    if robot.path and robot.path[0] == to:
        robot.path.pop(0)
    drain = float(ev.payload.get("drain", 0.0))
    robot.battery = max(0.0, robot.battery - drain)
    robot.energy += drain
    robot.distance += 1
    robot.wait_ticks = 0
    world.stats.distance_total += 1
    world.stats.energy_total += drain


@handles(EventType.ROBOT_WAITING)
def _robot_waiting(world: WorldState, ev: Event) -> None:
    robot = world.robots.get(ev.entity_id or "")
    if robot is None:
        return
    robot.wait_ticks += 1
    world.stats.wait_ticks_total += 1


@handles(EventType.ROBOT_STATUS_CHANGED)
def _robot_status_changed(world: WorldState, ev: Event) -> None:
    robot = world.robots.get(ev.entity_id or "")
    if robot is None:
        return
    robot.status = RobotStatus(ev.payload["status"])
    if "action_until_tick" in ev.payload:
        robot.action_until_tick = int(ev.payload["action_until_tick"])
    if "charger_id" in ev.payload:
        robot.charger_id = ev.payload["charger_id"]
    if "dock_id" in ev.payload:
        dock = world.docks.get(ev.payload["dock_id"])
        if dock is not None and robot.id not in dock.queue:
            dock.queue.append(robot.id)
    if "battery_delta" in ev.payload:
        delta = float(ev.payload["battery_delta"])
        robot.battery = min(100.0, max(0.0, robot.battery + delta))
        if delta < 0:
            robot.energy += -delta
            world.stats.energy_total += -delta


@handles(EventType.WAYPOINT_REACHED)
def _waypoint_reached(world: WorldState, ev: Event) -> None:
    robot = world.robots.get(ev.entity_id or "")
    if robot is None:
        return
    task = world.tasks.get(robot.task_id or "")
    if task is not None and task.leg < len(task.waypoints):
        task.waypoints[task.leg].done = True
        task.leg += 1


@handles(EventType.BATTERY_UPDATED)
def _battery_updated(world: WorldState, ev: Event) -> None:
    robot = world.robots.get(ev.entity_id or "")
    if robot is None:
        return
    delta = float(ev.payload.get("delta", 0.0))
    robot.battery = min(100.0, max(0.0, robot.battery + delta))
    if delta < 0:
        robot.energy += -delta
        world.stats.energy_total += -delta


@handles(EventType.ITEM_PICKED)
def _item_picked(world: WorldState, ev: Event) -> None:
    robot = world.robots.get(ev.entity_id or "")
    order = world.orders.get(ev.payload["order_id"])
    shelf = world.shelves.get(ev.payload["shelf_id"])
    sku = ev.payload["sku"]
    qty = int(ev.payload["qty"])
    if shelf is not None:
        shelf.inventory[sku] = max(0, shelf.inventory.get(sku, 0) - qty)
    if order is not None:
        for line in order.lines:
            if line.shelf_id == ev.payload["shelf_id"] and line.sku == sku and not line.picked:
                line.picked = True
                break
        if order.status == OrderStatus.ASSIGNED:
            order.started_tick = ev.tick
            world.mark_order_status(order, OrderStatus.IN_PROGRESS)
    if robot is not None:
        robot.load += qty
        task = world.tasks.get(robot.task_id or "")
        if task is not None and task.leg < len(task.waypoints):
            task.waypoints[task.leg].done = True
            task.leg += 1
    world.stats.picks_total += 1


@handles(EventType.ITEM_DROPPED)
def _item_dropped(world: WorldState, ev: Event) -> None:
    robot = world.robots.get(ev.entity_id or "")
    if robot is None:
        return
    task = world.tasks.get(robot.task_id or "")
    if task is not None and task.leg < len(task.waypoints):
        task.waypoints[task.leg].done = True
        task.leg += 1
    dock = world.docks.get(ev.payload.get("dock_id") or "")
    if dock is not None and robot.id in dock.queue:
        dock.queue.remove(robot.id)


@handles(EventType.BATTERY_LOW)
def _battery_low(world: WorldState, ev: Event) -> None:
    return  # informational; the charging trip is a TASK_CREATED / ROBOT_STATUS_CHANGED


@handles(EventType.CHARGING_STARTED)
def _charging_started(world: WorldState, ev: Event) -> None:
    robot = world.robots.get(ev.entity_id or "")
    charger = world.chargers.get(ev.payload["charger_id"])
    if robot is None or charger is None:
        return
    if robot.id not in charger.occupants:
        charger.occupants.append(robot.id)
    robot.charger_id = charger.id
    robot.status = RobotStatus.CHARGING
    robot.path = []
    task = world.tasks.get(robot.task_id or "")
    if task is not None and task.current is not None and task.current.kind.value == "charge":
        task.waypoints[task.leg].done = True
        task.leg += 1
    world.stats.charging_sessions += 1


@handles(EventType.CHARGING_COMPLETED)
def _charging_completed(world: WorldState, ev: Event) -> None:
    robot = world.robots.get(ev.entity_id or "")
    if robot is None:
        return
    charger = world.chargers.get(robot.charger_id or "")
    if charger is not None and robot.id in charger.occupants:
        charger.occupants.remove(robot.id)
    robot.charger_id = None
    robot.battery = float(ev.payload.get("battery", robot.battery))
    task = world.tasks.get(robot.task_id or "")
    if task is not None and task.status == TaskStatus.ACTIVE and task.current is not None:
        robot.status = RobotStatus.MOVING
    else:
        robot.status = RobotStatus.IDLE
        if task is not None and task.status == TaskStatus.ACTIVE:
            task.status = TaskStatus.COMPLETED
            task.completed_tick = ev.tick
            robot.task_id = None


@handles(EventType.ROBOT_FAILURE)
def _robot_failure(world: WorldState, ev: Event) -> None:
    robot = world.robots.get(ev.entity_id or "")
    if robot is None or robot.status == RobotStatus.FAILED:
        return
    task = world.tasks.get(robot.task_id or "")
    if task is not None and task.status in (TaskStatus.PLANNED, TaskStatus.ACTIVE):
        task.status = TaskStatus.CANCELLED
        task.completed_tick = ev.tick
        _release_orders_of_task(world, task, ev.tick)
    charger = world.chargers.get(robot.charger_id or "")
    if charger is not None and robot.id in charger.occupants:
        charger.occupants.remove(robot.id)
    robot.charger_id = None
    robot.task_id = None
    robot.path = []
    robot.load = 0
    robot.action_until_tick = 0
    robot.status = RobotStatus.FAILED
    robot.failure_cause = ev.payload.get("cause", "unknown")
    robot.failed_tick = ev.tick
    recovery = ev.payload.get("recovery_ticks")
    robot.recover_at_tick = ev.tick + int(recovery) if recovery is not None else None
    world.stats.failures_total += 1


@handles(EventType.ROBOT_RECOVERED)
def _robot_recovered(world: WorldState, ev: Event) -> None:
    robot = world.robots.get(ev.entity_id or "")
    if robot is None or robot.status != RobotStatus.FAILED:
        return
    robot.status = RobotStatus.IDLE
    robot.failure_cause = None
    robot.failed_tick = None
    robot.recover_at_tick = None


@handles(EventType.ROBOT_ADDED)
def _robot_added(world: WorldState, ev: Event) -> None:
    robot = Robot.from_dict(ev.payload["robot"])
    if robot.id in world.robots:
        return
    zone = world.grid.zone_of(robot.cell.x, robot.cell.y) or robot.zone_id
    robot.zone_id = zone
    world.robots[robot.id] = robot
    world.occupancy[robot.cell].append(robot.id)
    world.zone_occupancy[zone] += 1


@handles(EventType.ROBOT_REMOVED)
def _robot_removed(world: WorldState, ev: Event) -> None:
    robot = world.robots.get(ev.entity_id or "")
    if robot is None:
        return
    task = world.tasks.get(robot.task_id or "")
    if task is not None and task.status in (TaskStatus.PLANNED, TaskStatus.ACTIVE):
        task.status = TaskStatus.CANCELLED
        task.completed_tick = ev.tick
        _release_orders_of_task(world, task, ev.tick)
    charger = world.chargers.get(robot.charger_id or "")
    if charger is not None and robot.id in charger.occupants:
        charger.occupants.remove(robot.id)
    if robot.cell in world.occupancy and robot.id in world.occupancy[robot.cell]:
        world.occupancy[robot.cell].remove(robot.id)
    world.zone_occupancy[robot.zone_id] -= 1
    del world.robots[robot.id]


# ------------------------------------------------------------------------------------------------
# workers
# ------------------------------------------------------------------------------------------------


@handles(EventType.WORKER_DELAY)
def _worker_delay(world: WorldState, ev: Event) -> None:
    worker = world.workers.get(ev.entity_id or "")
    if worker is None:
        return
    worker.status = WorkerStatus.DELAYED
    worker.delay_until_tick = ev.tick + int(ev.payload.get("ticks", 600))


@handles(EventType.WORKER_STATUS_CHANGED)
def _worker_status_changed(world: WorldState, ev: Event) -> None:
    worker = world.workers.get(ev.entity_id or "")
    if worker is None:
        return
    worker.status = WorkerStatus(ev.payload["status"])
    if worker.status != WorkerStatus.DELAYED:
        worker.delay_until_tick = 0
    if "cell" in ev.payload:
        worker.cell = _cell(ev.payload["cell"])
        worker.zone_id = (
            ev.payload.get("zone_id") or world.grid.zone_of(worker.cell.x, worker.cell.y) or worker.zone_id
        )


# ------------------------------------------------------------------------------------------------
# infrastructure
# ------------------------------------------------------------------------------------------------


@handles(EventType.AISLE_BLOCKED)
def _aisle_blocked(world: WorldState, ev: Event) -> None:
    for c in ev.payload.get("cells", []):
        world.grid.block(_cell(c))
    world._zone_adjacency = None
    for robot in world.robots.values():
        if robot.path and any(world.grid.is_blocked(c) for c in robot.path):
            robot.path = []


@handles(EventType.AISLE_CLEARED)
def _aisle_cleared(world: WorldState, ev: Event) -> None:
    for c in ev.payload.get("cells", []):
        world.grid.unblock(_cell(c))
    world._zone_adjacency = None


@handles(EventType.ZONE_CLOSED)
def _zone_closed(world: WorldState, ev: Event) -> None:
    zone = world.zones.get(ev.entity_id or "")
    if zone is None:
        return
    zone.closed = True
    world.grid.close_zone(zone.id)
    for robot in world.robots.values():
        if robot.path and any(world.grid.zone_of(c.x, c.y) == zone.id for c in robot.path):
            robot.path = []


@handles(EventType.ZONE_OPENED)
def _zone_opened(world: WorldState, ev: Event) -> None:
    zone = world.zones.get(ev.entity_id or "")
    if zone is None:
        return
    zone.closed = False
    world.grid.open_zone(zone.id)


@handles(EventType.DOCK_CLOSED)
def _dock_closed(world: WorldState, ev: Event) -> None:
    dock = world.docks.get(ev.entity_id or "")
    if dock is not None:
        dock.open = False


@handles(EventType.DOCK_OPENED)
def _dock_opened(world: WorldState, ev: Event) -> None:
    dock = world.docks.get(ev.entity_id or "")
    if dock is not None:
        dock.open = True


@handles(EventType.CHARGER_DISABLED)
def _charger_disabled(world: WorldState, ev: Event) -> None:
    charger = world.chargers.get(ev.entity_id or "")
    if charger is not None:
        charger.enabled = False


@handles(EventType.CHARGER_ENABLED)
def _charger_enabled(world: WorldState, ev: Event) -> None:
    charger = world.chargers.get(ev.entity_id or "")
    if charger is not None:
        charger.enabled = True


# ------------------------------------------------------------------------------------------------
# inventory
# ------------------------------------------------------------------------------------------------


@handles(EventType.INVENTORY_MOVED)
def _inventory_moved(world: WorldState, ev: Event) -> None:
    src = world.shelves.get(ev.payload["from_shelf"])
    dst = world.shelves.get(ev.payload["to_shelf"])
    sku = ev.payload["sku"]
    if src is None or dst is None:
        return
    qty = min(int(ev.payload.get("qty", 0)), src.inventory.get(sku, 0))
    if qty <= 0:
        return
    src.inventory[sku] -= qty
    if src.inventory[sku] <= 0:
        del src.inventory[sku]
        shelves = world.sku_index.get(sku, [])
        if src.id in shelves:
            shelves.remove(src.id)
    dst.inventory[sku] = dst.inventory.get(sku, 0) + qty
    world.sku_index.setdefault(sku, [])
    if dst.id not in world.sku_index[sku]:
        world.sku_index[sku].append(dst.id)


@handles(EventType.INVENTORY_RESTOCKED)
def _inventory_restocked(world: WorldState, ev: Event) -> None:
    shelf = world.shelves.get(ev.payload["shelf_id"])
    if shelf is None:
        return
    sku = ev.payload["sku"]
    shelf.inventory[sku] = shelf.inventory.get(sku, 0) + int(ev.payload.get("qty", 0))
    world.sku_index.setdefault(sku, [])
    if shelf.id not in world.sku_index[sku]:
        world.sku_index[sku].append(shelf.id)


@handles(EventType.SHIPMENT_DEPARTED)
def _shipment_departed(world: WorldState, ev: Event) -> None:
    return


# ------------------------------------------------------------------------------------------------
# demand / config / plans / system
# ------------------------------------------------------------------------------------------------


@handles(EventType.DEMAND_CHANGED)
def _demand_changed(world: WorldState, ev: Event) -> None:
    d = world.demand
    p = ev.payload
    if "multiplier" in p:
        d.multiplier = float(p["multiplier"])
    if "orders_per_hour" in p:
        d.orders_per_hour = float(p["orders_per_hour"])
    if "burst_multiplier" in p:
        d.burst_multiplier = float(p["burst_multiplier"])
    if "burst_ticks" in p:
        d.burst_until_tick = ev.tick + int(p["burst_ticks"])
    if "burst_until_tick" in p:
        d.burst_until_tick = int(p["burst_until_tick"])


@handles(EventType.CONFIG_CHANGED)
def _config_changed(world: WorldState, ev: Event) -> None:
    cfg = world.config
    for key, value in ev.payload.items():
        if key in ("capacities", "strategy"):
            continue
        if key == "sla_minutes" and isinstance(value, dict):
            cfg.sla_minutes.update({k: float(v) for k, v in value.items()})
        elif hasattr(cfg, key):
            current = getattr(cfg, key)
            setattr(cfg, key, type(current)(value) if current is not None else value)
    if "capacities" in ev.payload:
        for zid, cap in ev.payload["capacities"].items():
            if zid in world.zones:
                world.zones[zid].capacity = int(cap)


@handles(EventType.PLAN_PROPOSED)
def _plan_proposed(world: WorldState, ev: Event) -> None:
    world.labels["last_plan_proposed"] = ev.payload.get("plan_id", "")


@handles(EventType.PLAN_APPROVED)
def _plan_approved(world: WorldState, ev: Event) -> None:
    world.labels["last_plan_approved"] = ev.payload.get("plan_id", "")


@handles(EventType.PLAN_REJECTED)
def _plan_rejected(world: WorldState, ev: Event) -> None:
    world.labels["last_plan_rejected"] = ev.payload.get("plan_id", "")


@handles(EventType.PLAN_EXECUTED)
def _plan_executed(world: WorldState, ev: Event) -> None:
    world.labels["last_plan_executed"] = ev.payload.get("plan_id", "")


@handles(EventType.TICK)
def _tick(world: WorldState, ev: Event) -> None:
    s = world.stats
    s.ticks += 1
    s.congestion_ticks_total += float(ev.payload.get("congestion", 0))
    s.productive_robot_ticks += int(ev.payload.get("productive", 0))
    s.operational_robot_ticks += int(ev.payload.get("operational", 0))
    idle_drain = world.config.battery_drain_idle
    if idle_drain > 0:
        for robot in world.robots.values():
            if robot.status.operational and robot.status != RobotStatus.CHARGING and robot.battery > 0:
                robot.battery = max(0.0, robot.battery - idle_drain)
                robot.energy += idle_drain
                s.energy_total += idle_drain


@handles(EventType.SNAPSHOT_TAKEN)
def _snapshot_taken(world: WorldState, ev: Event) -> None:
    world.labels["last_snapshot"] = ev.payload.get("digest", "")[:12]


SUPPORTED_TYPES = frozenset(_HANDLERS)
