"""``optimized`` scheduling strategy: batching + weighted EDF + CP-SAT assignment + congestion-aware
routing + pre-emptive charging. Registered with the simulation on import.

The strategy is deliberately *stateful and mutable*: the agents steer it at runtime by setting
``routing_policy`` (avoid / prefer zones), ``batch_max``, ``pending_charge`` (robots to send to a
charger after their current task) and ``priority_boost`` (order id → weight). It is picklable so
that plans can be evaluated in forked worlds and worker processes.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from nexus.events.types import Event, EventType
from nexus.optimization.engine import OptimizationEngine
from nexus.optimization.objective import ObjectiveWeights
from nexus.optimization.routing import CellCost, RoutingPolicy, zone_cell_index
from nexus.simulation.strategies import register_strategy
from nexus.twin.entities import Cell, Robot, RobotStatus

if TYPE_CHECKING:
    from nexus.simulation.engine import SimulationEngine

_REPLAN_TRIGGERS = frozenset(
    {
        EventType.ROBOT_FAILURE,
        EventType.ROBOT_RECOVERED,
        EventType.ZONE_CLOSED,
        EventType.ZONE_OPENED,
        EventType.DOCK_CLOSED,
        EventType.DOCK_OPENED,
        EventType.TASK_CANCELLED,
        EventType.ROBOT_ADDED,
    }
)


class OptimizedStrategy:
    name = "optimized"

    def __init__(
        self,
        assign_every: int = 5,
        method: str = "auto",
        batch_max: int = 3,
        routing_policy: RoutingPolicy | None = None,
        weights: ObjectiveWeights | None = None,
        time_limit_s: float = 0.2,
        preemptive_charging: bool = True,
        charge_margin: float = 10.0,
        max_batches_per_robot: int = 4,
        opportunistic_charge_below: float = 55.0,
    ) -> None:
        self.assign_every = max(1, assign_every)
        self.method = method
        self.batch_max = max(1, batch_max)
        self.routing_policy = routing_policy or RoutingPolicy()
        self.weights = weights
        self.time_limit_s = time_limit_s
        self.preemptive_charging = preemptive_charging
        self.charge_margin = charge_margin
        self.max_batches_per_robot = max_batches_per_robot
        # idle robots with nothing to do top up below this level instead of crowding the docks
        self.opportunistic_charge_below = opportunistic_charge_below
        self.pending_charge: set[str] = set()
        self.priority_boost: dict[str, float] = {}
        self.rounds = 0
        self.tasks_created = 0
        self.last_explanation: dict[str, Any] = {}
        self._dirty = True
        self._failed_signature: tuple[int, int, int] | None = None
        self._opt: OptimizationEngine | None = None
        self._cost_cache: tuple[int, int, CellCost | None] | None = None
        self._zone_cells: tuple[int, dict[str, list[int]]] | None = None
        self._last_costs: tuple[dict[str, float], CellCost] | None = None

    # ---- pickling (engine/world references are rebuilt lazily) ---------------------------------
    def __getstate__(self) -> dict[str, Any]:
        state = self.__dict__.copy()
        state["_opt"] = None
        state["_cost_cache"] = None
        state["_zone_cells"] = None
        state["_last_costs"] = None
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        self.__dict__.update(state)
        self._opt = None
        self._cost_cache = None
        self._zone_cells = None
        self._last_costs = None

    def _optimizer(self, engine: SimulationEngine) -> OptimizationEngine:
        if self._opt is None or self._opt.world is not engine.world:
            self._opt = OptimizationEngine(engine.world, engine.pathfinder, self.weights)
        return self._opt

    # ---- strategy protocol ---------------------------------------------------------------------
    def tick(self, engine: SimulationEngine) -> None:
        world = engine.world
        t = world.clock.tick
        if self.routing_policy.until_tick:
            self.routing_policy.expire(t)
        self._cost_cache = None
        if self.preemptive_charging:
            self._preemptive_charging(engine)
        # planning interval grows gently with fleet size (100 robots → every 10 ticks) to keep
        # large simulations fast; structural events (`_dirty`) always trigger an immediate round
        interval = max(self.assign_every, len(world.robots) // 10)
        if t % interval and not self._dirty:
            return
        self._dirty = False
        pending = world.pending_orders()
        if not pending:
            return
        threshold = world.config.battery_low_threshold
        robots = [
            r for r in world.available_robots() if r.battery >= threshold and r.id not in self.pending_charge
        ]
        if not robots:
            return
        signature = (len(pending), len(robots), world.grid.version)
        if signature == self._failed_signature:
            return
        plan = self._optimizer(engine).plan_assignments(
            robots,
            pending,
            method=self.method,
            batch_max=self.batch_max,
            time_limit_s=self.time_limit_s,
            routing_policy=self.routing_policy,
            origin=self.name,
            max_batches_per_robot=self.max_batches_per_robot,
            priority_boost=self.priority_boost or None,
        )
        self.rounds += 1
        self.last_explanation = self._opt.explain_last() if self._opt else {}
        if not plan.tasks:
            self._failed_signature = signature
            return
        self._failed_signature = None
        for task in plan.tasks:
            engine.emit(
                EventType.TASK_CREATED, task.robot_id, {"task": task.to_dict(), "method": plan.result.method}
            )
            self.tasks_created += 1

    def route(
        self, engine: SimulationEngine, robot: Robot, goal: Cell, avoid: Iterable[Cell] = ()
    ) -> list[Cell] | None:
        return engine.pathfinder.astar(robot.cell, goal, avoid=avoid, cost_fn=self._cost_fn(engine))

    def on_event(self, engine: SimulationEngine, event: Event) -> None:
        if event.type in _REPLAN_TRIGGERS:
            self._dirty = True
            self._failed_signature = None

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "assignment": f"CP-SAT ({self.method}) over batches, weighted-EDF sequencing",
            "routing": "A* with congestion-aware cost: " + self.routing_policy.describe(),
            "batching": self.batch_max > 1,
            "batch_max": self.batch_max,
            "assign_every": self.assign_every,
            "preemptive_charging": self.preemptive_charging,
            "opportunistic_charge_below": self.opportunistic_charge_below,
            "pending_charge": sorted(self.pending_charge),
            "priority_boosts": len(self.priority_boost),
            "rounds": self.rounds,
            "tasks_created": self.tasks_created,
            "last": dict(self.last_explanation),
        }

    # ---- internals -----------------------------------------------------------------------------
    def _cost_fn(self, engine: SimulationEngine) -> CellCost | None:
        world = engine.world
        key = (world.clock.tick, world.grid.version)
        if self._cost_cache is not None and self._cost_cache[:2] == key:
            return self._cost_cache[2]
        if self._zone_cells is None or self._zone_cells[0] != world.grid.version:
            self._zone_cells = (world.grid.version, zone_cell_index(world))
        zone_costs = self.routing_policy.zone_costs(world)
        if not zone_costs:
            fn: CellCost | None = None
        elif self._last_costs is not None and self._last_costs[0] == zone_costs:
            fn = self._last_costs[1]
        else:
            fn = self.routing_policy.cost_fn(world, self._zone_cells[1])
            if fn is not None:
                self._last_costs = (zone_costs, fn)
        self._cost_cache = (key[0], key[1], fn)
        return fn

    def _preemptive_charging(self, engine: SimulationEngine) -> None:
        world = engine.world
        cfg = world.config
        pending_exists: bool | None = None
        for rid in sorted(world.robots):
            robot = world.robots[rid]
            if robot.status != RobotStatus.IDLE or robot.task_id is not None:
                continue
            if robot.battery >= cfg.battery_charge_target:
                self.pending_charge.discard(rid)
                continue
            wanted = rid in self.pending_charge
            if not wanted and robot.battery < max(
                cfg.battery_low_threshold + self.charge_margin, self.opportunistic_charge_below
            ):
                if pending_exists is None:
                    pending_exists = bool(world.pending_orders())
                wanted = not pending_exists
            if not wanted:
                continue
            charger = engine._pick_charger(robot)
            if charger is None:
                continue
            engine.emit(
                EventType.BATTERY_LOW,
                rid,
                {"battery": round(robot.battery, 2), "charger_id": charger.id, "preemptive": True},
            )
            engine.emit(
                EventType.ROBOT_STATUS_CHANGED,
                rid,
                {"status": RobotStatus.TO_CHARGER.value, "charger_id": charger.id},
            )
            self.pending_charge.discard(rid)


class OptimizedGreedyStrategy(OptimizedStrategy):
    """Ablation: the same machinery with a greedy solver and no batching."""

    name = "optimized_greedy"

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("method", "greedy")
        kwargs.setdefault("batch_max", 1)
        super().__init__(**kwargs)


register_strategy("optimized", OptimizedStrategy)
register_strategy("optimized_greedy", OptimizedGreedyStrategy)
