"""Scheduling strategies.

A strategy makes *decisions* (which robot serves which orders, in which sequence, along which
path); the engine owns the *mechanics*. The baseline lives here; the optimization-backed strategies
live in :mod:`nexus.optimization.strategy` and the agentic ones in :mod:`nexus.agents`.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from nexus.events.types import Event, EventType
from nexus.simulation.tasks import choose_dock, make_task, resolve_lines
from nexus.twin.entities import Cell, Robot

if TYPE_CHECKING:
    from nexus.simulation.engine import SimulationEngine


@runtime_checkable
class Strategy(Protocol):
    name: str

    def tick(self, engine: SimulationEngine) -> None:
        """Called once per tick before robots move. Emit TASK_* events here."""
        ...

    def route(
        self, engine: SimulationEngine, robot: Robot, goal: Cell, avoid: Iterable[Cell] = ()
    ) -> list[Cell] | None: ...

    def on_event(self, engine: SimulationEngine, event: Event) -> None: ...

    def describe(self) -> dict: ...


class GreedyStrategy:
    """Baseline: FIFO orders, nearest available robot, plain shortest path, one order per trip."""

    name = "baseline"

    def __init__(self, assign_every: int = 1) -> None:
        self.assign_every = assign_every

    def tick(self, engine: SimulationEngine) -> None:
        world = engine.world
        if world.clock.tick % self.assign_every:
            return
        pending = world.pending_orders()
        if not pending:
            return
        threshold = world.config.battery_low_threshold
        robots = [r for r in world.available_robots() if r.battery >= threshold]
        if not robots:
            return
        pending.sort(key=lambda o: (o.created_tick, o.id))
        for order in pending:
            if not robots:
                break
            if not resolve_lines(world, order):
                continue
            first = world.shelves[order.lines[0].shelf_id].access_cell
            robot = min(robots, key=lambda r: (r.cell.manhattan(first), r.id))
            task = make_task(world, robot, [order], dock=choose_dock(world, first), origin=self.name)
            if task is None:
                continue
            robots.remove(robot)
            engine.emit(EventType.TASK_CREATED, robot.id, {"task": task.to_dict()})

    def route(
        self, engine: SimulationEngine, robot: Robot, goal: Cell, avoid: Iterable[Cell] = ()
    ) -> list[Cell] | None:
        return engine.pathfinder.astar(robot.cell, goal, avoid=avoid)

    def on_event(self, engine: SimulationEngine, event: Event) -> None:
        return

    def describe(self) -> dict:
        return {
            "name": self.name,
            "assignment": "nearest-idle-robot (FIFO)",
            "routing": "A* shortest path",
            "batching": False,
        }


STRATEGIES: dict[str, type] = {"baseline": GreedyStrategy}


def register_strategy(name: str, cls: type) -> None:
    STRATEGIES[name] = cls


def make_strategy(name: str, **kwargs: object) -> Strategy:
    if name not in STRATEGIES:
        # optimization / agent strategies register themselves on import
        import importlib

        for mod in ("nexus.optimization.strategy", "nexus.agents.strategy"):
            try:
                importlib.import_module(mod)
            except ImportError:
                continue
    if name not in STRATEGIES:
        raise ValueError(f"unknown strategy {name!r}; available: {sorted(STRATEGIES)}")
    return STRATEGIES[name](**kwargs)  # type: ignore[no-any-return]
