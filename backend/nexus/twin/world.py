"""The world state — single source of truth of the digital twin.

Design rules:

* The world is mutated **only** by the event reducer (:mod:`nexus.events.reducer`). Engines and agents
  *emit events*; they never poke at entities directly.
* :meth:`WorldState.fork` produces an isolated simulation world (RNG state, clock, id counters
  included) — the base of every "what if" and every simulate-before-execute check.
* :meth:`WorldState.digest` is a deterministic hash of everything that matters. Two worlds with the
  same digest are behaviourally identical; the test-suite relies on this to prove determinism and
  replay correctness.
"""

from __future__ import annotations

import contextlib
import hashlib
import pickle
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import orjson

from nexus.core.clock import SimClock
from nexus.core.ids import IdGen
from nexus.core.rng import SeededRNG
from nexus.twin.entities import (
    Cell,
    ChargingStation,
    Conveyor,
    DemandProfile,
    LoadingDock,
    Order,
    OrderStatus,
    Robot,
    RobotStatus,
    RunningStats,
    Shelf,
    SimConfig,
    Task,
    TaskStatus,
    Worker,
    Zone,
    ZoneKind,
)
from nexus.twin.spatial import GridMap


@dataclass
class WorldState:
    name: str
    domain: str
    seed: int
    scale: str
    grid: GridMap
    zones: dict[str, Zone] = field(default_factory=dict)
    shelves: dict[str, Shelf] = field(default_factory=dict)
    chargers: dict[str, ChargingStation] = field(default_factory=dict)
    docks: dict[str, LoadingDock] = field(default_factory=dict)
    conveyors: dict[str, Conveyor] = field(default_factory=dict)
    workers: dict[str, Worker] = field(default_factory=dict)
    robots: dict[str, Robot] = field(default_factory=dict)
    orders: dict[str, Order] = field(default_factory=dict)
    tasks: dict[str, Task] = field(default_factory=dict)
    sku_index: dict[str, list[str]] = field(default_factory=dict)  # sku -> shelf ids
    sku_popularity: dict[str, float] = field(default_factory=dict)
    clock: SimClock = field(default_factory=SimClock)
    rng: SeededRNG = field(default_factory=SeededRNG)
    ids: IdGen = field(default_factory=IdGen)
    demand: DemandProfile = field(default_factory=DemandProfile)
    config: SimConfig = field(default_factory=SimConfig)
    stats: RunningStats = field(default_factory=RunningStats)
    version: int = 0
    is_fork: bool = False
    labels: dict[str, str] = field(default_factory=dict)  # free-form annotations (scenario name, plan id…)

    # derived caches (never hashed, rebuilt on restore)
    occupancy: dict[Cell, list[str]] = field(
        default_factory=lambda: defaultdict(list), repr=False, compare=False
    )
    zone_occupancy: dict[str, int] = field(
        default_factory=lambda: defaultdict(int), repr=False, compare=False
    )
    _zone_adjacency: dict[str, set[str]] | None = field(default=None, repr=False, compare=False)
    _open_order_ids: set[str] = field(default_factory=set, repr=False, compare=False)

    # ---- lifecycle -----------------------------------------------------------------------------
    def rebuild_caches(self) -> None:
        self.occupancy = defaultdict(list)
        self.zone_occupancy = defaultdict(int)
        for robot in self.robots.values():
            self.occupancy[robot.cell].append(robot.id)
            self.zone_occupancy[robot.zone_id] += 1
        self._open_order_ids = {o.id for o in self.orders.values() if o.status.open}
        self._zone_adjacency = None

    def fork(self, label: str | None = None) -> WorldState:
        """Deep, isolated copy. pickle round-trip is ~5× faster than ``copy.deepcopy`` here."""
        clone: WorldState = pickle.loads(pickle.dumps(self, protocol=pickle.HIGHEST_PROTOCOL))
        clone.is_fork = True
        if label:
            clone.labels = {**clone.labels, "fork": label}
        clone.rebuild_caches()
        return clone

    def snapshot_bytes(self) -> bytes:
        return pickle.dumps(self, protocol=pickle.HIGHEST_PROTOCOL)

    @staticmethod
    def from_snapshot(data: bytes) -> WorldState:
        world: WorldState = pickle.loads(data)
        world.rebuild_caches()
        return world

    def __getstate__(self) -> dict[str, Any]:
        state = self.__dict__.copy()
        state["occupancy"] = None
        state["zone_occupancy"] = None
        state["_zone_adjacency"] = None
        state["_open_order_ids"] = None
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        self.__dict__.update(state)
        self.rebuild_caches()

    # ---- spatial helpers -----------------------------------------------------------------------
    def zone_at(self, cell: Cell) -> str | None:
        return self.grid.zone_of(cell.x, cell.y)

    def zone_adjacency(self) -> dict[str, set[str]]:
        if self._zone_adjacency is None:
            self._zone_adjacency = self.grid.zone_adjacency()
        return self._zone_adjacency

    def robots_in_zone(self, zone_id: str) -> list[Robot]:
        return [r for r in self.robots.values() if r.zone_id == zone_id]

    def zone_congestion(self, zone_id: str) -> int:
        zone = self.zones[zone_id]
        return max(0, self.zone_occupancy.get(zone_id, 0) - zone.capacity)

    def congestion_total(self) -> int:
        return sum(max(0, self.zone_occupancy.get(z.id, 0) - z.capacity) for z in self.zones.values())

    def cell_capacity(self, cell: Cell) -> int:
        ctype = self.grid.cell_type(cell.x, cell.y)
        if ctype.value in (3, 4, 6):  # dock, charger, staging: shared cells
            return 99
        zone_id = self.grid.zone_of(cell.x, cell.y)
        if zone_id is not None:
            zone = self.zones.get(zone_id)
            if zone is not None and zone.kind == ZoneKind.CORRIDOR:
                return self.config.corridor_cell_capacity
        return self.config.cell_capacity

    def cell_free(self, cell: Cell, for_robot: str | None = None) -> bool:
        occ = self.occupancy.get(cell, ())
        n = len(occ) - (1 if for_robot in occ else 0)
        return n < self.cell_capacity(cell)

    def place_robot(self, robot: Robot, cell: Cell) -> None:
        """Move a robot on the grid, maintaining occupancy caches. Called by the reducer only."""
        old = robot.cell
        if old in self.occupancy:
            with contextlib.suppress(ValueError):
                self.occupancy[old].remove(robot.id)
            if not self.occupancy[old]:
                del self.occupancy[old]
        robot.cell = cell
        self.occupancy[cell].append(robot.id)
        new_zone = self.grid.zone_of(cell.x, cell.y) or robot.zone_id
        if new_zone != robot.zone_id:
            self.zone_occupancy[robot.zone_id] -= 1
            self.zone_occupancy[new_zone] += 1
            robot.zone_id = new_zone

    # ---- entity helpers ------------------------------------------------------------------------
    def open_orders(self) -> list[Order]:
        return [self.orders[oid] for oid in self._open_order_ids if oid in self.orders]

    def pending_orders(self) -> list[Order]:
        return [o for o in self.open_orders() if o.status == OrderStatus.PENDING]

    def mark_order_status(self, order: Order, status: OrderStatus) -> None:
        order.status = status
        if status.open:
            self._open_order_ids.add(order.id)
        else:
            self._open_order_ids.discard(order.id)

    def available_robots(self) -> list[Robot]:
        return [r for r in self.robots.values() if r.available]

    def operational_robots(self) -> list[Robot]:
        return [r for r in self.robots.values() if r.status.operational]

    def failed_robots(self) -> list[Robot]:
        return [r for r in self.robots.values() if r.status == RobotStatus.FAILED]

    def active_tasks(self) -> list[Task]:
        return [t for t in self.tasks.values() if t.status in (TaskStatus.PLANNED, TaskStatus.ACTIVE)]

    def open_docks(self) -> list[LoadingDock]:
        return [d for d in self.docks.values() if d.open]

    def storage_zones(self) -> list[Zone]:
        return [z for z in self.zones.values() if z.kind == ZoneKind.STORAGE]

    def corridor_zones(self) -> list[Zone]:
        return [z for z in self.zones.values() if z.kind == ZoneKind.CORRIDOR]

    def shelves_in_zone(self, zone_id: str) -> list[Shelf]:
        return [s for s in self.shelves.values() if s.zone_id == zone_id]

    def inventory_units(self) -> int:
        return sum(s.units for s in self.shelves.values())

    # ---- hashing / serialization ---------------------------------------------------------------
    def digest(self) -> str:
        """Deterministic hash of all behaviour-relevant state."""
        h = hashlib.sha256()
        h.update(self.grid.digest().encode())
        canonical = {
            "tick": self.clock.tick,
            "version": self.version,
            "ids": self.ids.counters,
            "rng": hashlib.sha256(repr(self.rng.getstate()).encode()).hexdigest(),
            "robots": [
                (
                    r.id,
                    r.cell.x,
                    r.cell.y,
                    round(r.battery, 4),
                    r.status.value,
                    r.task_id,
                    r.load,
                    r.distance,
                    r.charger_id,
                )
                for r in sorted(self.robots.values(), key=lambda r: r.id)
            ],
            "orders": [
                (o.id, o.status.value, o.robot_id, o.task_id, o.delivered_tick)
                for o in sorted(self.orders.values(), key=lambda o: o.id)
            ],
            "tasks": [
                (t.id, t.robot_id, t.leg, t.status.value)
                for t in sorted(self.tasks.values(), key=lambda t: t.id)
            ],
            "chargers": [
                (c.id, c.enabled, sorted(c.occupants))
                for c in sorted(self.chargers.values(), key=lambda c: c.id)
            ],
            "docks": [(d.id, d.open, d.delivered) for d in sorted(self.docks.values(), key=lambda d: d.id)],
            "shelves": [
                (s.id, sorted(s.inventory.items())) for s in sorted(self.shelves.values(), key=lambda s: s.id)
            ],
            "zones": [(z.id, z.closed, z.capacity) for z in sorted(self.zones.values(), key=lambda z: z.id)],
            "workers": [
                (w.id, w.status.value, w.delay_until_tick)
                for w in sorted(self.workers.values(), key=lambda w: w.id)
            ],
            "stats": self.stats.to_dict(),
            "demand": self.demand.to_dict(),
            "config": self.config.to_dict(),
        }
        h.update(orjson.dumps(canonical, option=orjson.OPT_SORT_KEYS))
        return h.hexdigest()

    def summary(self) -> dict[str, Any]:
        robots = list(self.robots.values())
        operational = [r for r in robots if r.status.operational]
        open_orders = self.open_orders()
        return {
            "name": self.name,
            "domain": self.domain,
            "scale": self.scale,
            "seed": self.seed,
            "tick": self.clock.tick,
            "sim_time": self.clock.now().isoformat(),
            "version": self.version,
            "is_fork": self.is_fork,
            "robots_total": len(robots),
            "robots_operational": len(operational),
            "robots_failed": len(robots) - len(operational),
            "robots_charging": sum(1 for r in robots if r.status == RobotStatus.CHARGING),
            "workers": len(self.workers),
            "zones": len(self.zones),
            "shelves": len(self.shelves),
            "inventory_units": self.inventory_units(),
            "orders_open": len(open_orders),
            "orders_pending": sum(1 for o in open_orders if o.status == OrderStatus.PENDING),
            "orders_delivered": self.stats.orders_delivered,
            "orders_late": self.stats.orders_late,
            "tasks_active": len(self.active_tasks()),
            "congestion": self.congestion_total(),
            "blocked_cells": len(self.grid.blocked),
            "closed_zones": sorted(self.grid.closed_zones),
            "labels": dict(self.labels),
        }

    def to_dict(
        self, orders: str = "open", include_grid: bool = True, max_orders: int = 400
    ) -> dict[str, Any]:
        if orders == "all":
            order_objs = list(self.orders.values())
        elif orders == "open":
            order_objs = sorted(self.open_orders(), key=lambda o: o.created_tick)
        else:
            order_objs = []
        payload: dict[str, Any] = {
            "summary": self.summary(),
            "clock": self.clock.to_dict(),
            "zones": [z.to_dict() for z in self.zones.values()],
            "robots": [r.to_dict() for r in self.robots.values()],
            "workers": [w.to_dict() for w in self.workers.values()],
            "docks": [d.to_dict() for d in self.docks.values()],
            "chargers": [c.to_dict() for c in self.chargers.values()],
            "conveyors": [c.to_dict() for c in self.conveyors.values()],
            "orders": [o.to_dict() for o in order_objs[:max_orders]],
            "tasks": [t.to_dict() for t in self.active_tasks()],
            "stats": self.stats.to_dict(),
            "demand": self.demand.to_dict(),
            "config": self.config.to_dict(),
            "zone_occupancy": dict(self.zone_occupancy),
        }
        if include_grid:
            payload["grid"] = self.grid.to_dict()
            payload["shelves"] = [s.to_dict() for s in self.shelves.values()]
        return payload
