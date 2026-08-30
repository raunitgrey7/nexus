"""Feasibility constraints shared by every solver and by the plan validator.

The optimizer is only allowed to propose assignments that satisfy:

* robot capacity (items per trip) and robot availability / operational status,
* battery: ``battery ≥ reserve · (cells · drain_move + picks · pick_ticks · drain_action) + ½·low_threshold``,
* sourceability: every order line can be picked from a shelf with stock in an open zone,
* batch size limits and one-robot-per-task / one-task-per-order exclusivity.
"""

from __future__ import annotations

from nexus.simulation.tasks import battery_needed, choose_dock, estimate_cells, order_waypoints, resolve_lines
from nexus.twin.entities import Order, OrderStatus, Robot, Task, Waypoint, WaypointKind
from nexus.twin.world import WorldState


def battery_requirement(world: WorldState, cells: int, picks: int) -> float:
    """Battery percentage points a trip needs, including the engine's safety margin."""
    return battery_needed(world, cells, picks) + world.config.battery_low_threshold * 0.5


def trip_estimate(world: WorldState, robot: Robot, orders: list[Order]) -> tuple[int, int, str | None]:
    """(estimated cells, number of picks, dock id) for ``robot`` serving ``orders`` from its current cell."""
    picks = order_waypoints(world, robot.cell, orders)
    if not picks:
        return 0, 0, None
    dock = choose_dock(world, picks[-1].cell)
    if dock is None:
        return 0, len(picks), None
    cells = estimate_cells(robot.cell, [*picks, Waypoint(WaypointKind.DELIVER, dock.id, dock.cell, None)])
    return cells, len(picks), dock.id


def assignment_feasible(
    world: WorldState, robot: Robot, orders: list[Order], batch_max: int | None = None
) -> tuple[bool, str]:
    """Can ``robot`` serve ``orders`` as one trip right now? Returns ``(ok, reason)``.

    Note: like :func:`nexus.simulation.tasks.make_task`, this re-targets order lines to shelves that
    actually have stock (``resolve_lines``); that is the only mutation it performs.
    """
    cfg = world.config
    if not robot.status.operational:
        return False, f"{robot.id} is {robot.status.value}"
    if not robot.available:
        return False, f"{robot.id} is busy ({robot.status.value})"
    if robot.battery < cfg.battery_low_threshold:
        return (
            False,
            f"{robot.id} battery {robot.battery:.0f}% below threshold {cfg.battery_low_threshold:.0f}%",
        )
    if not orders:
        return False, "no orders"
    limit = max(1, batch_max if batch_max is not None else cfg.batch_max_orders)
    if len(orders) > limit:
        return False, f"batch of {len(orders)} exceeds batch_max {limit}"
    for order in orders:
        if order.status != OrderStatus.PENDING:
            return False, f"{order.id} is {order.status.value}, not pending"
        if not resolve_lines(world, order):
            return False, f"{order.id} cannot be sourced (stock-out or closed zone)"
    items = sum(o.items for o in orders)
    if items > robot.capacity:
        return False, f"{items} items exceed {robot.id} capacity {robot.capacity}"
    cells, picks, dock_id = trip_estimate(world, robot, orders)
    if dock_id is None:
        return False, "no open loading dock"
    need = battery_requirement(world, cells, picks)
    if robot.battery < need:
        return False, f"{robot.id} battery {robot.battery:.0f}% < {need:.0f}% needed for {cells} cells"
    return True, "ok"


def validate_tasks(world: WorldState, tasks: list[Task]) -> list[str]:
    """Structural validation of a set of tasks about to be emitted. Empty list = valid."""
    errors: list[str] = []
    seen_robots: set[str] = set()
    seen_orders: set[str] = set()
    for task in tasks:
        robot = world.robots.get(task.robot_id)
        if robot is None:
            errors.append(f"{task.id}: unknown robot {task.robot_id}")
            continue
        if task.robot_id in seen_robots:
            errors.append(f"{task.id}: robot {task.robot_id} assigned twice in this plan")
        seen_robots.add(task.robot_id)
        if robot.task_id and robot.task_id != task.id:
            errors.append(f"{task.id}: robot {task.robot_id} already executing {robot.task_id}")
        if not robot.status.operational:
            errors.append(f"{task.id}: robot {task.robot_id} is {robot.status.value}")
        if not task.order_ids:
            errors.append(f"{task.id}: no orders")
        for oid in task.order_ids:
            if oid in seen_orders:
                errors.append(f"{task.id}: order {oid} appears in two tasks")
            seen_orders.add(oid)
            order = world.orders.get(oid)
            if order is None:
                errors.append(f"{task.id}: unknown order {oid}")
            elif order.task_id and order.task_id != task.id:
                errors.append(f"{task.id}: order {oid} already assigned to {order.task_id}")
            elif not order.status.open:
                errors.append(f"{task.id}: order {oid} is {order.status.value}")
        if not task.waypoints:
            errors.append(f"{task.id}: no waypoints")
            continue
        for wp in task.waypoints:
            if not world.grid.walkable(wp.cell.x, wp.cell.y):
                errors.append(f"{task.id}: waypoint {wp.kind.value} at {tuple(wp.cell)} is not walkable")
        last = task.waypoints[-1]
        if last.kind == WaypointKind.DELIVER:
            dock = world.docks.get(last.target_id)
            if dock is None or not dock.open:
                errors.append(f"{task.id}: delivery dock {last.target_id} is closed or unknown")
        items = sum(world.orders[o].items for o in task.order_ids if o in world.orders)
        if items > robot.capacity:
            errors.append(f"{task.id}: {items} items exceed capacity {robot.capacity}")
    return errors
