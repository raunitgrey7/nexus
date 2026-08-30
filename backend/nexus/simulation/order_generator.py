"""Stochastic order arrivals driven by the world's demand profile and seeded RNG."""

from __future__ import annotations

from bisect import bisect_left
from typing import TYPE_CHECKING

from nexus.events.types import EventType
from nexus.twin.entities import Order, OrderLine, OrderPriority
from nexus.twin.world import WorldState

if TYPE_CHECKING:
    from nexus.simulation.engine import SimulationEngine

LINE_COUNT_WEIGHTS = [0.45, 0.30, 0.15, 0.10]


class OrderGenerator:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self._skus: list[str] = []
        self._cum: list[float] = []
        self._prepared_for: int | None = None

    def _prepare(self, world: WorldState) -> None:
        key = id(world.sku_popularity)
        if self._prepared_for == key and self._skus:
            return
        items = sorted(world.sku_popularity.items())
        self._skus = [k for k, _ in items]
        acc = 0.0
        cum = []
        for _, p in items:
            acc += p
            cum.append(acc)
        self._cum = cum
        self._prepared_for = key

    def tick(self, engine: SimulationEngine) -> int:
        if not self.enabled:
            return 0
        world = engine.world
        rate = world.demand.rate_per_tick(
            world.clock.hour_of_day(), world.clock.tick, world.clock.tick_seconds
        )
        n = world.rng.poisson(rate)
        for _ in range(n):
            order = self.make_order(world)
            engine.emit(EventType.ORDER_CREATED, order.id, {"order": order.to_dict()})
        return n

    def make_order(self, world: WorldState) -> Order:
        self._prepare(world)
        rng = world.rng
        demand = world.demand
        n_lines = min(demand.max_lines, rng.weighted_index(LINE_COUNT_WEIGHTS) + 1)
        lines: list[OrderLine] = []
        seen: set[str] = set()
        attempts = 0
        while len(lines) < n_lines and attempts < n_lines * 4:
            attempts += 1
            r = rng.random() * self._cum[-1]
            sku = self._skus[min(bisect_left(self._cum, r), len(self._skus) - 1)]
            if sku in seen:
                continue
            shelf_ids = world.sku_index.get(sku)
            if not shelf_ids:
                continue
            qty = rng.randint(1, demand.max_qty)
            stocked = [sid for sid in shelf_ids if world.shelves[sid].inventory.get(sku, 0) >= qty]
            shelf_id = (
                max(stocked, key=lambda sid: (world.shelves[sid].inventory.get(sku, 0), sid))
                if stocked
                else shelf_ids[0]
            )
            seen.add(sku)
            lines.append(OrderLine(sku=sku, qty=qty, shelf_id=shelf_id))
        if not lines:  # extremely unlikely, but never emit an empty order
            sku = self._skus[0]
            lines.append(OrderLine(sku=sku, qty=1, shelf_id=world.sku_index[sku][0]))
        priority = OrderPriority(rng.weighted_index(demand.priority_weights))
        tick = world.clock.tick
        return Order(
            id=world.ids.next("ORD"),
            created_tick=tick,
            deadline_tick=tick + world.config.sla_ticks(priority),
            priority=priority,
            lines=lines,
        )
