"""Task construction: turning orders into a robot's waypoint sequence.

Shared by the baseline scheduler, the optimizer and the plan executor so that every strategy
produces tasks the engine understands.
"""

from __future__ import annotations

from nexus.twin.entities import (
    Cell,
    LoadingDock,
    Order,
    OrderLine,
    Robot,
    Task,
    Waypoint,
    WaypointKind,
)
from nexus.twin.world import WorldState

DETOUR_FACTOR = 1.25  # manhattan → expected walking distance in an aisle layout


def resolve_lines(world: WorldState, order: Order) -> bool:
    """Ensure every line points at a shelf that has stock. Re-targets lines when needed.

    Returns ``False`` if some line cannot be sourced anywhere in the warehouse right now.
    """
    for line in order.lines:
        if line.picked:
            continue
        shelf = world.shelves.get(line.shelf_id)
        if shelf is not None and shelf.inventory.get(line.sku, 0) >= line.qty:
            zone = world.zones.get(shelf.zone_id)
            if zone is not None and not zone.closed:
                continue
        # find an alternative shelf with stock in an open zone
        best: str | None = None
        best_qty = 0
        for sid in world.sku_index.get(line.sku, []):
            alt = world.shelves.get(sid)
            if alt is None:
                continue
            zone = world.zones.get(alt.zone_id)
            if zone is not None and zone.closed:
                continue
            qty = alt.inventory.get(line.sku, 0)
            if qty >= line.qty and qty > best_qty:
                best, best_qty = sid, qty
        if best is None:
            return False
        line.shelf_id = best
    return True


def choose_dock(world: WorldState, near: Cell, prefer: str | None = None) -> LoadingDock | None:
    docks = world.open_docks()
    if not docks:
        return None
    if prefer and prefer in world.docks and world.docks[prefer].open:
        return world.docks[prefer]
    return min(docks, key=lambda d: (d.cell.manhattan(near) + 3 * len(d.queue), d.id))


def order_waypoints(world: WorldState, start: Cell, orders: list[Order]) -> list[Waypoint]:
    """Nearest-neighbour sequencing of all pick locations of ``orders`` starting from ``start``."""
    pending: list[tuple[Cell, Waypoint]] = []
    for order in orders:
        for line in order.lines:
            if line.picked:
                continue
            shelf = world.shelves[line.shelf_id]
            pending.append(
                (shelf.access_cell, Waypoint(WaypointKind.PICK, shelf.id, shelf.access_cell, order.id))
            )
    out: list[Waypoint] = []
    cur = start
    while pending:
        i = min(
            range(len(pending)),
            key=lambda k: (
                pending[k][0].manhattan(cur),
                pending[k][1].order_id or "",
                pending[k][1].target_id,
            ),
        )
        cell, wp = pending.pop(i)
        out.append(wp)
        cur = cell
    return out


def estimate_cells(start: Cell, waypoints: list[Waypoint]) -> int:
    total = 0
    cur = start
    for wp in waypoints:
        total += cur.manhattan(wp.cell)
        cur = wp.cell
    return int(total * DETOUR_FACTOR)


def battery_needed(world: WorldState, cells: int, actions: int = 0) -> float:
    cfg = world.config
    return (
        cells * cfg.battery_drain_move + actions * cfg.pick_ticks * cfg.battery_drain_action
    ) * cfg.battery_reserve_factor


def make_task(
    world: WorldState,
    robot: Robot,
    orders: list[Order],
    dock: LoadingDock | None = None,
    origin: str = "scheduler",
    check_battery: bool = True,
) -> Task | None:
    """Build a task for ``robot`` serving ``orders``. Returns ``None`` if infeasible."""
    if not orders:
        return None
    for order in orders:
        if not resolve_lines(world, order):
            return None
    items = sum(o.items for o in orders)
    if items > robot.capacity:
        return None
    picks = order_waypoints(world, robot.cell, orders)
    if not picks:
        return None
    last = picks[-1].cell
    dock = dock or choose_dock(world, last)
    if dock is None:
        return None
    waypoints = [*picks, Waypoint(WaypointKind.DELIVER, dock.id, dock.cell, None)]
    if check_battery:
        cells = estimate_cells(robot.cell, waypoints)
        need = battery_needed(world, cells, len(picks)) + world.config.battery_low_threshold * 0.5
        if robot.battery < need:
            return None
    task_id = world.ids.next("TASK")
    return Task(
        id=task_id,
        robot_id=robot.id,
        order_ids=[o.id for o in orders],
        waypoints=waypoints,
        created_tick=world.clock.tick,
        origin=origin,
    )


def task_span_zones(world: WorldState, task: Task) -> set[str]:
    zones: set[str] = set()
    for wp in task.waypoints:
        z = world.grid.zone_of(wp.cell.x, wp.cell.y)
        if z:
            zones.add(z)
    return zones


def lines_for_shelf(order: Order, shelf_id: str) -> list[OrderLine]:
    return [line for line in order.lines if line.shelf_id == shelf_id and not line.picked]
