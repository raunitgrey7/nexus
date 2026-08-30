"""Shared helpers for the forecasters (task statistics, zone demand, unit conversions)."""

from __future__ import annotations

from nexus.twin.entities import Order, Robot, RobotStatus, Task, TaskStatus
from nexus.twin.world import WorldState

DEFAULT_TASK_TICKS = 150.0  # used until the twin has completed a few tasks


def recent_completed_tasks(world: WorldState, limit: int = 300) -> list[Task]:
    """Most recent completed tasks (dict insertion order is chronological)."""
    out: list[Task] = []
    for task in reversed(list(world.tasks.values())):
        if task.status == TaskStatus.COMPLETED and task.completed_tick is not None:
            out.append(task)
            if len(out) >= limit:
                break
    return out


def mean_task_ticks(world: WorldState, default: float = DEFAULT_TASK_TICKS) -> float:
    """Mean duration (ticks) of recently completed tasks; ``default`` until ≥ 3 tasks completed."""
    tasks = recent_completed_tasks(world)
    if len(tasks) < 3:
        return default
    total = sum((t.completed_tick or 0) - t.created_tick for t in tasks)
    return max(1.0, total / len(tasks))


def mean_orders_per_task(world: WorldState) -> float:
    tasks = recent_completed_tasks(world)
    if not tasks:
        return max(1.0, float(world.config.batch_max_orders))
    return max(1.0, sum(len(t.order_ids) for t in tasks) / len(tasks))


def world_utilization(world: WorldState, default: float = 0.7) -> float:
    """Productive share of operational robot-ticks so far (``default`` for a fresh world)."""
    st = world.stats
    if st.operational_robot_ticks < 60:
        return default
    return min(1.0, st.productive_robot_ticks / st.operational_robot_ticks)


def order_zones(world: WorldState, order: Order) -> set[str]:
    """Zones the order still needs to visit (unpicked lines)."""
    zones: set[str] = set()
    for line in order.lines:
        if line.picked:
            continue
        shelf = world.shelves.get(line.shelf_id)
        if shelf is not None:
            zones.add(shelf.zone_id)
    return zones


def pending_fifo(world: WorldState) -> list[Order]:
    """Pending orders in the sequence the baseline dispatcher consumes them."""
    return sorted(world.pending_orders(), key=lambda o: (o.created_tick, o.id))


def dispatchable_robots(world: WorldState) -> list[Robot]:
    threshold = world.config.battery_low_threshold
    return [r for r in world.available_robots() if r.battery >= threshold]


def busy_robots(world: WorldState) -> list[Robot]:
    return [r for r in world.robots.values() if r.status.operational and r.task_id is not None]


def charging_robots(world: WorldState) -> list[Robot]:
    return [r for r in world.robots.values() if r.status in (RobotStatus.CHARGING, RobotStatus.TO_CHARGER)]


def ticks_to_minutes(world: WorldState, ticks: float) -> float:
    return ticks * world.clock.tick_seconds / 60.0


def minutes_to_ticks(world: WorldState, minutes: float) -> int:
    return round(minutes * 60.0 / world.clock.tick_seconds)


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))
