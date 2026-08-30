"""Order sequencing: priority-weighted Earliest-Deadline-First.

Plain EDF ignores priority; plain priority ordering starves old NORMAL orders. NEXUS uses a
*weighted deadline*::

    key(order) = now + slack(order) / w(priority)        slack = deadline − now (ticks)

so a CRITICAL order (w = 3) with 9 minutes of slack sorts like a NORMAL order with 3 minutes of
slack. Overdue orders (slack < 0) are multiplied instead of divided so that high-priority overdue
orders sort first of all. Optional per-order boosts (set by plans such as ``REPRIORITIZE_ORDERS``)
scale the weight.
"""

from __future__ import annotations

from nexus.optimization.objective import DEFAULT_WEIGHTS, ObjectiveWeights
from nexus.twin.entities import Order, OrderPriority
from nexus.twin.world import WorldState

PRIORITY_WEIGHT: dict[OrderPriority, float] = {
    OrderPriority.LOW: 0.6,
    OrderPriority.NORMAL: 1.0,
    OrderPriority.HIGH: 1.8,
    OrderPriority.CRITICAL: 3.0,
}


def priority_weight(
    order: Order, weights: ObjectiveWeights | None = None, boost: dict[str, float] | None = None
) -> float:
    w = (weights or DEFAULT_WEIGHTS).priority * PRIORITY_WEIGHT[order.priority]
    if boost:
        w *= boost.get(order.id, 1.0)
    return max(0.05, w)


def order_urgency(
    world: WorldState,
    order: Order,
    weights: ObjectiveWeights | None = None,
    boost: dict[str, float] | None = None,
) -> float:
    """Priority-scaled share of the SLA already consumed: 0 fresh · 1 at deadline · > 1 overdue."""
    ts = world.clock.tick_seconds
    slack_min = (order.deadline_tick - world.clock.tick) * ts / 60.0
    sla_min = float(world.config.sla_minutes.get(order.priority.name, 10.0))
    consumed = 1.0 - slack_min / max(sla_min, 1e-6)
    return round(consumed * priority_weight(order, weights, boost), 4)


def weighted_deadline(
    world: WorldState,
    order: Order,
    weights: ObjectiveWeights | None = None,
    boost: dict[str, float] | None = None,
) -> float:
    now = world.clock.tick
    slack = order.deadline_tick - now
    w = priority_weight(order, weights, boost)
    return now + (slack / w if slack >= 0 else slack * w)


def sequence_orders(
    world: WorldState,
    orders: list[Order],
    weights: ObjectiveWeights | None = None,
    boost: dict[str, float] | None = None,
) -> list[Order]:
    """Most urgent first (weighted EDF); ties broken by creation time then id for determinism."""
    return sorted(orders, key=lambda o: (weighted_deadline(world, o, weights, boost), o.created_tick, o.id))
