"""Entity model of the digital twin.

Entities are plain dataclasses (fast to copy, trivial to hash) rather than ORM/pydantic objects.
Pydantic is used only at the edges (API schemas, LLM structured output). Every entity exposes
``to_dict()`` for serialization and the world hash.

The engine only depends on the *shape* of these entities; the warehouse layout generator decides
how many of each exist and where. Other domains (factory, hospital) reuse the same shapes with a
different :class:`~nexus.twin.domain.DomainModel`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import IntEnum, StrEnum
from typing import NamedTuple


class Cell(NamedTuple):
    x: int
    y: int

    def manhattan(self, other: Cell) -> int:
        return abs(self.x - other.x) + abs(self.y - other.y)


class CellType(IntEnum):
    FLOOR = 0
    SHELF = 1
    WALL = 2
    DOCK = 3
    CHARGER = 4
    CONVEYOR = 5
    STAGING = 6

    @property
    def walkable(self) -> bool:
        return self in _WALKABLE


_WALKABLE = {CellType.FLOOR, CellType.DOCK, CellType.CHARGER, CellType.STAGING}


class ZoneKind(StrEnum):
    STORAGE = "storage"
    CORRIDOR = "corridor"
    DOCK = "dock"
    CHARGING = "charging"
    STAGING = "staging"


class RobotStatus(StrEnum):
    IDLE = "idle"
    MOVING = "moving"  # travelling to a pick location
    PICKING = "picking"
    DELIVERING = "delivering"  # travelling to a dock
    UNLOADING = "unloading"  # handing over at a dock
    TO_CHARGER = "to_charger"
    CHARGING = "charging"
    WAITING = "waiting"  # blocked by congestion
    FAILED = "failed"
    MAINTENANCE = "maintenance"

    @property
    def productive(self) -> bool:
        return self in _PRODUCTIVE

    @property
    def operational(self) -> bool:
        return self not in (RobotStatus.FAILED, RobotStatus.MAINTENANCE)


_PRODUCTIVE = {RobotStatus.MOVING, RobotStatus.PICKING, RobotStatus.DELIVERING, RobotStatus.UNLOADING}


class OrderStatus(StrEnum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"

    @property
    def open(self) -> bool:
        return self in (OrderStatus.PENDING, OrderStatus.ASSIGNED, OrderStatus.IN_PROGRESS)


class OrderPriority(IntEnum):
    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


class TaskStatus(StrEnum):
    PLANNED = "planned"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class WorkerStatus(StrEnum):
    AVAILABLE = "available"
    BUSY = "busy"
    BREAK = "break"
    ABSENT = "absent"
    DELAYED = "delayed"


class WaypointKind(StrEnum):
    PICK = "pick"
    DELIVER = "deliver"
    CHARGE = "charge"
    MOVE = "move"


# ------------------------------------------------------------------------------------------------
# static / infrastructure entities
# ------------------------------------------------------------------------------------------------


@dataclass(slots=True)
class Zone:
    id: str
    name: str
    kind: ZoneKind
    x0: int
    y0: int
    x1: int  # inclusive
    y1: int  # inclusive
    capacity: int = 3  # robots before the zone is considered congested
    closed: bool = False

    def contains(self, cell: Cell) -> bool:
        return self.x0 <= cell.x <= self.x1 and self.y0 <= cell.y <= self.y1

    @property
    def center(self) -> Cell:
        return Cell((self.x0 + self.x1) // 2, (self.y0 + self.y1) // 2)

    @property
    def area(self) -> int:
        return (self.x1 - self.x0 + 1) * (self.y1 - self.y0 + 1)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class Shelf:
    id: str
    cell: Cell
    access_cell: Cell  # walkable cell a robot stands on to pick
    zone_id: str
    inventory: dict[str, int] = field(default_factory=dict)  # sku -> quantity

    @property
    def units(self) -> int:
        return sum(self.inventory.values())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "cell": list(self.cell),
            "access_cell": list(self.access_cell),
            "zone_id": self.zone_id,
            "inventory": dict(self.inventory),
        }


@dataclass(slots=True)
class ChargingStation:
    id: str
    cell: Cell
    zone_id: str
    slots: int = 1
    enabled: bool = True
    occupants: list[str] = field(default_factory=list)

    @property
    def free_slots(self) -> int:
        return max(0, self.slots - len(self.occupants)) if self.enabled else 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "cell": list(self.cell),
            "zone_id": self.zone_id,
            "slots": self.slots,
            "enabled": self.enabled,
            "occupants": list(self.occupants),
        }


@dataclass(slots=True)
class LoadingDock:
    id: str
    cell: Cell
    zone_id: str
    open: bool = True
    queue: list[str] = field(default_factory=list)  # robot ids unloading here
    delivered: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "cell": list(self.cell),
            "zone_id": self.zone_id,
            "open": self.open,
            "queue": list(self.queue),
            "delivered": self.delivered,
        }


@dataclass(slots=True)
class Conveyor:
    id: str
    cells: list[Cell]
    zone_id: str
    active: bool = True

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "cells": [list(c) for c in self.cells],
            "zone_id": self.zone_id,
            "active": self.active,
        }


@dataclass(slots=True)
class Worker:
    id: str
    name: str
    role: str  # picker | packer | loader | supervisor
    cell: Cell
    zone_id: str
    status: WorkerStatus = WorkerStatus.AVAILABLE
    delay_until_tick: int = 0
    orders_handled: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["cell"] = list(self.cell)
        return d


# ------------------------------------------------------------------------------------------------
# dynamic entities
# ------------------------------------------------------------------------------------------------


@dataclass(slots=True)
class OrderLine:
    sku: str
    qty: int
    shelf_id: str
    picked: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> OrderLine:
        return OrderLine(
            sku=d["sku"], qty=int(d["qty"]), shelf_id=d["shelf_id"], picked=bool(d.get("picked", False))
        )


@dataclass(slots=True)
class Order:
    id: str
    created_tick: int
    deadline_tick: int
    priority: OrderPriority
    lines: list[OrderLine]
    status: OrderStatus = OrderStatus.PENDING
    task_id: str | None = None
    robot_id: str | None = None
    dock_id: str | None = None
    started_tick: int | None = None
    delivered_tick: int | None = None
    cancelled_tick: int | None = None

    @property
    def items(self) -> int:
        return sum(line.qty for line in self.lines)

    @property
    def shelf_ids(self) -> list[str]:
        return [line.shelf_id for line in self.lines]

    @property
    def is_late(self) -> bool:
        return self.delivered_tick is not None and self.delivered_tick > self.deadline_tick

    def fulfillment_ticks(self) -> int | None:
        if self.delivered_tick is None:
            return None
        return self.delivered_tick - self.created_tick

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "created_tick": self.created_tick,
            "deadline_tick": self.deadline_tick,
            "priority": int(self.priority),
            "priority_name": self.priority.name,
            "lines": [line.to_dict() for line in self.lines],
            "status": self.status.value,
            "task_id": self.task_id,
            "robot_id": self.robot_id,
            "dock_id": self.dock_id,
            "started_tick": self.started_tick,
            "delivered_tick": self.delivered_tick,
            "cancelled_tick": self.cancelled_tick,
            "items": self.items,
        }

    @staticmethod
    def from_dict(d: dict) -> Order:
        return Order(
            id=d["id"],
            created_tick=int(d["created_tick"]),
            deadline_tick=int(d["deadline_tick"]),
            priority=OrderPriority(int(d["priority"])),
            lines=[OrderLine.from_dict(x) for x in d["lines"]],
            status=OrderStatus(d.get("status", "pending")),
            task_id=d.get("task_id"),
            robot_id=d.get("robot_id"),
            dock_id=d.get("dock_id"),
            started_tick=d.get("started_tick"),
            delivered_tick=d.get("delivered_tick"),
            cancelled_tick=d.get("cancelled_tick"),
        )


@dataclass(slots=True)
class Waypoint:
    kind: WaypointKind
    target_id: str  # shelf id, dock id, charger id or "" for a free move
    cell: Cell
    order_id: str | None = None
    done: bool = False

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "target_id": self.target_id,
            "cell": list(self.cell),
            "order_id": self.order_id,
            "done": self.done,
        }

    @staticmethod
    def from_dict(d: dict) -> Waypoint:
        return Waypoint(
            kind=WaypointKind(d["kind"]),
            target_id=d.get("target_id", ""),
            cell=Cell(int(d["cell"][0]), int(d["cell"][1])),
            order_id=d.get("order_id"),
            done=bool(d.get("done", False)),
        )


@dataclass(slots=True)
class Task:
    """A unit of work for one robot: a sequence of waypoints serving one or more orders (a batch)."""

    id: str
    robot_id: str
    order_ids: list[str]
    waypoints: list[Waypoint]
    created_tick: int
    status: TaskStatus = TaskStatus.PLANNED
    leg: int = 0  # index of the current waypoint
    completed_tick: int | None = None
    origin: str = "scheduler"  # scheduler | optimizer | plan:<id>

    @property
    def current(self) -> Waypoint | None:
        return self.waypoints[self.leg] if self.leg < len(self.waypoints) else None

    @property
    def remaining(self) -> list[Waypoint]:
        return self.waypoints[self.leg :]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "robot_id": self.robot_id,
            "order_ids": list(self.order_ids),
            "waypoints": [w.to_dict() for w in self.waypoints],
            "created_tick": self.created_tick,
            "status": self.status.value,
            "leg": self.leg,
            "completed_tick": self.completed_tick,
            "origin": self.origin,
        }

    @staticmethod
    def from_dict(d: dict) -> Task:
        return Task(
            id=d["id"],
            robot_id=d["robot_id"],
            order_ids=list(d["order_ids"]),
            waypoints=[Waypoint.from_dict(w) for w in d["waypoints"]],
            created_tick=int(d["created_tick"]),
            status=TaskStatus(d.get("status", "planned")),
            leg=int(d.get("leg", 0)),
            completed_tick=d.get("completed_tick"),
            origin=d.get("origin", "scheduler"),
        )


@dataclass(slots=True)
class Robot:
    id: str
    cell: Cell
    zone_id: str
    battery: float = 100.0
    status: RobotStatus = RobotStatus.IDLE
    task_id: str | None = None
    path: list[Cell] = field(default_factory=list)  # remaining cells to traverse (excluding current)
    speed: float = 1.0  # cells per tick
    capacity: int = 10  # items
    load: int = 0
    action_until_tick: int = 0  # the tick at which the current pick / unload action completes
    wait_ticks: int = 0  # consecutive ticks blocked
    distance: int = 0
    energy: float = 0.0  # battery percentage points consumed
    productive_ticks: int = 0
    operational_ticks: int = 0
    failure_cause: str | None = None
    failed_tick: int | None = None
    recover_at_tick: int | None = None
    charger_id: str | None = None
    tasks_completed: int = 0
    orders_completed: int = 0

    @property
    def available(self) -> bool:
        return (
            self.status.operational
            and self.task_id is None
            and self.status
            not in (
                RobotStatus.CHARGING,
                RobotStatus.TO_CHARGER,
            )
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "cell": list(self.cell),
            "zone_id": self.zone_id,
            "battery": round(self.battery, 2),
            "status": self.status.value,
            "task_id": self.task_id,
            "path": [list(c) for c in self.path[:64]],
            "speed": self.speed,
            "capacity": self.capacity,
            "load": self.load,
            "action_until_tick": self.action_until_tick,
            "wait_ticks": self.wait_ticks,
            "distance": self.distance,
            "energy": round(self.energy, 3),
            "productive_ticks": self.productive_ticks,
            "operational_ticks": self.operational_ticks,
            "failure_cause": self.failure_cause,
            "failed_tick": self.failed_tick,
            "recover_at_tick": self.recover_at_tick,
            "charger_id": self.charger_id,
            "tasks_completed": self.tasks_completed,
            "orders_completed": self.orders_completed,
        }

    @staticmethod
    def from_dict(d: dict) -> Robot:
        return Robot(
            id=d["id"],
            cell=Cell(int(d["cell"][0]), int(d["cell"][1])),
            zone_id=d["zone_id"],
            battery=float(d.get("battery", 100.0)),
            status=RobotStatus(d.get("status", "idle")),
            speed=float(d.get("speed", 1.0)),
            capacity=int(d.get("capacity", 10)),
        )


# ------------------------------------------------------------------------------------------------
# configuration objects that live inside the world (so forks carry them)
# ------------------------------------------------------------------------------------------------


@dataclass(slots=True)
class DemandProfile:
    """Order arrival model: base rate × hour-of-day multiplier × global multiplier."""

    orders_per_hour: float = 400.0  # base rate; × hourly multiplier (10-hour operating window 08:00-18:00)
    hourly_multipliers: list[float] = field(
        default_factory=lambda: [
            0.05,
            0.05,
            0.05,
            0.05,
            0.05,
            0.05,  # 00-05 night
            0.1,
            0.3,
            0.8,
            1.0,
            1.15,
            1.25,  # 06-11 ramp-up to the late-morning peak
            1.2,
            1.0,
            1.1,
            1.2,
            1.0,
            0.7,  # 12-17 lunch dip, afternoon peak, wind-down
            0.3,
            0.15,
            0.1,
            0.05,
            0.05,
            0.05,  # 18-23
        ]
    )
    multiplier: float = 1.0
    max_lines: int = 4
    max_qty: int = 2
    priority_weights: list[float] = field(default_factory=lambda: [0.15, 0.70, 0.12, 0.03])  # LOW..CRITICAL
    burst_until_tick: int = 0
    burst_multiplier: float = 1.0

    def rate_per_tick(self, hour: int, tick: int, tick_seconds: int) -> float:
        rate = self.orders_per_hour * self.hourly_multipliers[hour % 24] * self.multiplier
        if tick < self.burst_until_tick:
            rate *= self.burst_multiplier
        return rate * tick_seconds / 3600.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class SimConfig:
    tick_seconds: int = 1
    robot_speed: float = 1.0  # cells per tick
    congestion_speed_factor: float = 0.6
    cell_capacity: int = 2
    corridor_cell_capacity: int = 4
    battery_drain_move: float = 0.02  # % per cell
    battery_drain_idle: float = 0.0005  # % per tick
    battery_drain_action: float = 0.01  # % per action tick
    battery_charge_rate: float = 0.15  # % per tick
    battery_low_threshold: float = 20.0
    battery_charge_target: float = 90.0
    battery_reserve_factor: float = 1.3  # required battery = predicted consumption × factor
    pick_ticks: int = 6
    unload_ticks: int = 4
    sla_minutes: dict[str, float] = field(
        default_factory=lambda: {"LOW": 20.0, "NORMAL": 10.0, "HIGH": 5.0, "CRITICAL": 3.0}
    )
    max_wait_before_replan: int = 6
    unreachable_cancel_ticks: int = 60
    unload_no_loader_factor: float = 2.0
    robot_failure_rate_per_hour: float = 0.0  # spontaneous failures per robot-hour
    failure_recovery_minutes: float = 30.0
    replenish_every_ticks: int = 300
    replenish_threshold: int = 6
    replenish_target: int = 24
    batch_max_orders: int = 1  # >1 enables order batching (optimizer sets this)
    task_rebalance: bool = False

    def sla_ticks(self, priority: OrderPriority) -> int:
        return int(self.sla_minutes[priority.name] * 60 / self.tick_seconds)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class RunningStats:
    """Monotonic counters maintained by the reducer; KPIs derive from these + entity state."""

    orders_created: int = 0
    orders_delivered: int = 0
    orders_late: int = 0
    orders_cancelled: int = 0
    fulfillment_ticks_total: int = 0
    lateness_ticks_total: int = 0
    distance_total: int = 0
    energy_total: float = 0.0
    picks_total: int = 0
    congestion_ticks_total: float = 0.0  # Σ over ticks of Σ zones max(0, occ - cap)
    wait_ticks_total: int = 0
    replans_total: int = 0
    failures_total: int = 0
    charging_sessions: int = 0
    ticks: int = 0
    productive_robot_ticks: int = 0
    operational_robot_ticks: int = 0

    def to_dict(self) -> dict:
        return asdict(self)
