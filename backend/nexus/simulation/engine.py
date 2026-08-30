"""The deterministic discrete-time simulation engine.

One ``step()`` = one tick. Order of operations inside a tick is fixed (order arrivals → faults →
recoveries → workers → replenishment → strategy decisions → robot kinematics in id order → hooks →
TICK), which, together with the seeded RNG, makes every run reproducible.

The engine never mutates the world directly: it reads state, decides, and **emits events** that the
reducer applies. The same engine runs the live twin and every forked simulation world.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from nexus.events.bus import EventBus
from nexus.events.reducer import apply
from nexus.events.store import DuplicateEventError, EventStore
from nexus.events.types import Event, EventType, make_event
from nexus.simulation.faults import FaultInjector
from nexus.simulation.metrics import KPIs, compute_kpis
from nexus.simulation.order_generator import OrderGenerator
from nexus.simulation.pathfinding import Pathfinder
from nexus.simulation.strategies import GreedyStrategy, Strategy
from nexus.twin.entities import (
    Cell,
    ChargingStation,
    LoadingDock,
    Robot,
    RobotStatus,
    Task,
    TaskStatus,
    WaypointKind,
    WorkerStatus,
)
from nexus.twin.world import WorldState

Hook = Callable[["SimulationEngine"], None]


class SimulationEngine:
    def __init__(
        self,
        world: WorldState,
        strategy: Strategy | None = None,
        store: EventStore | None = None,
        bus: EventBus | None = None,
        order_generator: OrderGenerator | None = None,
        fault_injector: FaultInjector | None = None,
    ) -> None:
        self.world = world
        self.strategy: Strategy = strategy or GreedyStrategy()
        self.store = store or EventStore()
        self.bus = bus or EventBus()
        self.orders = order_generator or OrderGenerator()
        self.faults = fault_injector or FaultInjector()
        self.pathfinder = Pathfinder(world)
        self.hooks: list[Hook] = []
        self.ticks_run = 0
        self.started_tick = world.clock.tick

    # ---- event emission ------------------------------------------------------------------------
    def emit(
        self,
        type_: EventType,
        entity_id: str | None = None,
        payload: dict[str, Any] | None = None,
        origin: str = "engine",
        key: str | None = None,
        cause: str | None = None,
    ) -> Event:
        ev = make_event(type_, self.world.clock.tick, entity_id, payload, origin, key, cause)
        self.store.append(ev)
        apply(self.world, ev)
        if self.bus.has_subscribers:
            self.bus.publish(ev)
        self.strategy.on_event(self, ev)
        return ev

    def inject(
        self,
        type_: EventType,
        entity_id: str | None = None,
        payload: dict[str, Any] | None = None,
        origin: str = "external",
        key: str | None = None,
        cause: str | None = None,
    ) -> Event | None:
        """Apply an external command idempotently. Returns ``None`` if ``key`` was already seen."""
        if key is not None and self.store.has_key(key):
            return None
        try:
            return self.emit(type_, entity_id, payload, origin=origin, key=key, cause=cause)
        except DuplicateEventError:
            return None

    # ---- main loop -----------------------------------------------------------------------------
    def step(self) -> None:
        world = self.world
        t = world.clock.tick
        self.orders.tick(self)
        self.faults.tick(self)
        self._recoveries(t)
        self._workers(t)
        if world.config.replenish_every_ticks and t % world.config.replenish_every_ticks == 0 and t > 0:
            self._replenish()
        self.strategy.tick(self)
        robots = world.robots
        for rid in sorted(robots):
            self._advance_robot(robots[rid], t)
        for hook in self.hooks:
            hook(self)
        productive = 0
        operational = 0
        for r in robots.values():
            if r.status.operational:
                operational += 1
                if r.status.productive:
                    productive += 1
        self.emit(
            EventType.TICK,
            None,
            {"congestion": world.congestion_total(), "productive": productive, "operational": operational},
        )
        world.clock.advance()
        self.ticks_run += 1

    def run(self, ticks: int) -> None:
        for _ in range(ticks):
            self.step()

    def run_until(self, tick: int) -> None:
        while self.world.clock.tick < tick:
            self.step()

    def kpis(self, since_tick: int | None = None) -> KPIs:
        return compute_kpis(self.world, since_tick if since_tick is not None else 0)

    # ---- periodic processes --------------------------------------------------------------------
    def _recoveries(self, t: int) -> None:
        for rid in sorted(self.world.robots):
            robot = self.world.robots[rid]
            if (
                robot.status == RobotStatus.FAILED
                and robot.recover_at_tick is not None
                and robot.recover_at_tick <= t
            ):
                self.emit(EventType.ROBOT_RECOVERED, rid, {"after_ticks": t - (robot.failed_tick or t)})

    def _workers(self, t: int) -> None:
        for wid in sorted(self.world.workers):
            worker = self.world.workers[wid]
            if worker.status == WorkerStatus.DELAYED and worker.delay_until_tick <= t:
                self.emit(EventType.WORKER_STATUS_CHANGED, wid, {"status": WorkerStatus.AVAILABLE.value})

    def _replenish(self) -> None:
        world = self.world
        cfg = world.config
        for sku in sorted(world.sku_index):
            shelf_ids = world.sku_index[sku]
            total = sum(world.shelves[s].inventory.get(sku, 0) for s in shelf_ids if s in world.shelves)
            if total >= cfg.replenish_threshold:
                continue
            target = max(shelf_ids, key=lambda s: (world.shelves[s].inventory.get(sku, 0), s))
            self.emit(
                EventType.INVENTORY_RESTOCKED,
                target,
                {"shelf_id": target, "sku": sku, "qty": cfg.replenish_target - total},
            )

    # ---- robot kinematics ----------------------------------------------------------------------
    def _advance_robot(self, r: Robot, t: int) -> None:
        world = self.world
        cfg = world.config
        if not r.status.operational:
            return
        if r.status == RobotStatus.CHARGING:
            delta = min(cfg.battery_charge_rate, 100.0 - r.battery)
            if delta > 0:
                self.emit(EventType.BATTERY_UPDATED, r.id, {"delta": delta})
            if r.battery >= cfg.battery_charge_target - 1e-9:
                self.emit(
                    EventType.CHARGING_COMPLETED, r.id, {"charger_id": r.charger_id, "battery": r.battery}
                )
            return
        if r.battery <= 0.0:
            self.emit(
                EventType.ROBOT_FAILURE,
                r.id,
                {
                    "cause": "battery_depleted",
                    "recovery_ticks": int(cfg.failure_recovery_minutes * 60 / world.clock.tick_seconds),
                },
            )
            return
        if r.action_until_tick > t:
            return  # picking / unloading in progress
        task = world.tasks.get(r.task_id) if r.task_id else None
        if task is not None and task.status != TaskStatus.ACTIVE:
            task = None

        # ---- finish an action that completes this tick --------------------------------------
        if r.action_until_tick == t and task is not None and task.current is not None:
            wp = task.current
            if r.status == RobotStatus.PICKING and wp.kind == WaypointKind.PICK:
                self._complete_pick(r, task, wp.order_id or "", wp.target_id)
                return
            if r.status == RobotStatus.UNLOADING and wp.kind == WaypointKind.DELIVER:
                self._complete_unload(r, task, wp.target_id, t)
                return

        # ---- no task: charging trips / idle ---------------------------------------------------
        if task is None:
            if r.status == RobotStatus.TO_CHARGER:
                charger = world.chargers.get(r.charger_id or "")
                if charger is None or not charger.enabled:
                    self.emit(
                        EventType.ROBOT_STATUS_CHANGED,
                        r.id,
                        {"status": RobotStatus.IDLE.value, "charger_id": None},
                    )
                    return
                if r.cell == charger.cell:
                    self.emit(EventType.CHARGING_STARTED, r.id, {"charger_id": charger.id})
                else:
                    self._move(r, charger.cell, t)
                return
            if r.battery < cfg.battery_low_threshold:
                charger = self._pick_charger(r)
                if charger is not None:
                    self.emit(
                        EventType.BATTERY_LOW,
                        r.id,
                        {"battery": round(r.battery, 2), "charger_id": charger.id},
                    )
                    self.emit(
                        EventType.ROBOT_STATUS_CHANGED,
                        r.id,
                        {"status": RobotStatus.TO_CHARGER.value, "charger_id": charger.id},
                    )
                return
            if r.status not in (RobotStatus.IDLE, RobotStatus.WAITING):
                self.emit(EventType.ROBOT_STATUS_CHANGED, r.id, {"status": RobotStatus.IDLE.value})
            return

        # ---- task execution -------------------------------------------------------------------
        nxt = task.current
        if nxt is None:
            self.emit(EventType.TASK_COMPLETED, r.id, {"task_id": task.id, "orders": list(task.order_ids)})
            return
        if r.cell == nxt.cell:
            if nxt.kind == WaypointKind.PICK:
                self.emit(
                    EventType.ROBOT_STATUS_CHANGED,
                    r.id,
                    {"status": RobotStatus.PICKING.value, "action_until_tick": t + cfg.pick_ticks},
                )
            elif nxt.kind == WaypointKind.DELIVER:
                dock = world.docks.get(nxt.target_id)
                factor = (
                    1.0 if dock is not None and self._dock_has_loader(dock) else cfg.unload_no_loader_factor
                )
                duration = max(1, math.ceil(cfg.unload_ticks * factor))
                self.emit(
                    EventType.ROBOT_STATUS_CHANGED,
                    r.id,
                    {
                        "status": RobotStatus.UNLOADING.value,
                        "action_until_tick": t + duration,
                        "dock_id": nxt.target_id,
                    },
                )
            elif nxt.kind == WaypointKind.CHARGE:
                self.emit(EventType.CHARGING_STARTED, r.id, {"charger_id": nxt.target_id})
            else:
                self.emit(EventType.WAYPOINT_REACHED, r.id, {"task_id": task.id, "leg": task.leg})
            return
        desired = RobotStatus.DELIVERING if nxt.kind == WaypointKind.DELIVER else RobotStatus.MOVING
        if nxt.kind == WaypointKind.CHARGE:
            desired = RobotStatus.TO_CHARGER
        if r.status != desired and r.status != RobotStatus.WAITING:
            self.emit(EventType.ROBOT_STATUS_CHANGED, r.id, {"status": desired.value})
        self._move(r, nxt.cell, t)

    def _complete_pick(self, r: Robot, task: Task, order_id: str, shelf_id: str) -> None:
        world = self.world
        order = world.orders.get(order_id)
        if order is None or not order.status.open:
            self.emit(EventType.WAYPOINT_REACHED, r.id, {"task_id": task.id, "leg": task.leg})
            return
        line = next((ln for ln in order.lines if ln.shelf_id == shelf_id and not ln.picked), None)
        if line is None:
            self.emit(EventType.WAYPOINT_REACHED, r.id, {"task_id": task.id, "leg": task.leg})
            return
        shelf = world.shelves.get(shelf_id)
        available = shelf.inventory.get(line.sku, 0) if shelf else 0
        if available < line.qty:
            # stock vanished; re-route this line elsewhere by cancelling the task (orders go back to pending)
            self.emit(
                EventType.TASK_CANCELLED,
                r.id,
                {"task_id": task.id, "reason": "stockout", "shelf_id": shelf_id},
            )
            return
        self.emit(
            EventType.ITEM_PICKED,
            r.id,
            {
                "order_id": order.id,
                "shelf_id": shelf_id,
                "sku": line.sku,
                "qty": line.qty,
                "task_id": task.id,
            },
        )

    def _complete_unload(self, r: Robot, task: Task, dock_id: str, t: int) -> None:
        world = self.world
        delivered: list[str] = []
        for oid in task.order_ids:
            order = world.orders.get(oid)
            if order is None or not order.status.open:
                continue
            if all(line.picked for line in order.lines):
                self.emit(
                    EventType.ORDER_DELIVERED,
                    oid,
                    {
                        "order_id": oid,
                        "robot_id": r.id,
                        "dock_id": dock_id,
                        "late": t > order.deadline_tick,
                        "task_id": task.id,
                    },
                )
                delivered.append(oid)
        self.emit(
            EventType.ITEM_DROPPED, r.id, {"dock_id": dock_id, "order_ids": delivered, "task_id": task.id}
        )
        if task.current is None:
            self.emit(EventType.TASK_COMPLETED, r.id, {"task_id": task.id, "orders": list(task.order_ids)})

    def _dock_has_loader(self, dock: LoadingDock) -> bool:
        for worker in self.world.workers.values():
            if worker.role != "loader":
                continue
            if worker.cell.x == dock.cell.x and worker.status in (WorkerStatus.AVAILABLE, WorkerStatus.BUSY):
                return True
        return False

    def _pick_charger(self, r: Robot) -> ChargingStation | None:
        world = self.world
        reserved: dict[str, int] = {}
        for other in world.robots.values():
            if other.charger_id and other.status == RobotStatus.TO_CHARGER:
                reserved[other.charger_id] = reserved.get(other.charger_id, 0) + 1
        best: ChargingStation | None = None
        best_key: tuple[int, str] | None = None
        for c in world.chargers.values():
            if not c.enabled:
                continue
            if c.free_slots - reserved.get(c.id, 0) <= 0:
                continue
            key = (c.cell.manhattan(r.cell), c.id)
            if best_key is None or key < best_key:
                best, best_key = c, key
        return best

    # ---- movement ------------------------------------------------------------------------------
    def _move(self, r: Robot, goal: Cell, t: int) -> None:
        world = self.world
        cfg = world.config
        if not r.path or r.path[-1] != goal:
            path = self.strategy.route(self, r, goal)
            if path is None:
                self.emit(
                    EventType.ROBOT_WAITING,
                    r.id,
                    {"cell": list(r.cell), "reason": "unreachable", "goal": list(goal)},
                )
                if r.task_id and r.wait_ticks >= cfg.unreachable_cancel_ticks:
                    self.emit(EventType.TASK_CANCELLED, r.id, {"task_id": r.task_id, "reason": "unreachable"})
                elif (
                    not r.task_id
                    and r.status == RobotStatus.TO_CHARGER
                    and r.wait_ticks >= cfg.unreachable_cancel_ticks
                ):
                    self.emit(
                        EventType.ROBOT_STATUS_CHANGED,
                        r.id,
                        {"status": RobotStatus.IDLE.value, "charger_id": None},
                    )
                return
            self.emit(
                EventType.ROBOT_PATH_SET,
                r.id,
                {"path": [list(c) for c in path], "goal": list(goal), "replan": r.wait_ticks > 0},
            )
            if not path:
                return
        speed = r.speed
        if world.zone_congestion(r.zone_id) > 0:
            speed *= cfg.congestion_speed_factor
        if speed < 1.0 and math.floor(t * speed) == math.floor((t - 1) * speed):
            return  # deterministic slow-down: skip this tick
        nxt = r.path[0]
        if not world.grid.walkable(nxt.x, nxt.y):
            self.emit(EventType.ROBOT_PATH_SET, r.id, {"path": [], "goal": list(goal), "replan": True})
            return
        if world.cell_free(nxt, for_robot=r.id):
            self.emit(
                EventType.ROBOT_MOVED,
                r.id,
                {"from": list(r.cell), "to": list(nxt), "drain": cfg.battery_drain_move},
            )
            return
        self.emit(
            EventType.ROBOT_WAITING,
            r.id,
            {"cell": list(nxt), "blocked_by": list(world.occupancy.get(nxt, []))},
        )
        if r.wait_ticks >= cfg.max_wait_before_replan:
            path = self.strategy.route(self, r, goal, avoid=(nxt,))
            if path:
                self.emit(
                    EventType.ROBOT_PATH_SET,
                    r.id,
                    {"path": [list(c) for c in path], "goal": list(goal), "replan": True},
                )

    # ---- introspection -------------------------------------------------------------------------
    def describe(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy.describe(),
            "tick": self.world.clock.tick,
            "ticks_run": self.ticks_run,
            "events": self.store.stats(),
            "pathfinder": self.pathfinder.stats(),
            "scheduled_faults": [f.to_dict() for f in self.faults.remaining()],
        }
