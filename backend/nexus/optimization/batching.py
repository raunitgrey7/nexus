"""Order batching: grouping pending orders into multi-order trips.

A robot that already walks to Zone C can pick a second order from Zone C (or the neighbouring
zone) at almost no extra cost. Batching is the single biggest capacity lever in the platform: it
lifts baseline capacity by roughly 1.6-2× on the small layout (see ``docs/BENCHMARKS.md``).

Rules (greedy, deterministic — the input order is the urgency sequence from
:mod:`nexus.optimization.scheduling`):

* the most urgent unbatched order seeds a batch;
* candidates are added in urgency order while items fit the robot capacity and the batch stays
  below ``batch_max``;
* spatial compatibility — every zone a candidate touches must be within ``max_hops`` storage-zone
  hops of a zone the batch already touches (0 = same zone, ~1 = across one corridor);
* deadline protection — a CRITICAL/HIGH seed only accepts same-zone companions and CRITICAL seeds
  are capped at two orders, so urgent orders are never delayed by far-away picks.
"""

from __future__ import annotations

from nexus.twin.entities import Order, OrderPriority
from nexus.twin.world import WorldState


def order_zones(world: WorldState, order: Order) -> set[str]:
    zones: set[str] = set()
    for line in order.lines:
        if line.picked:
            continue
        shelf = world.shelves.get(line.shelf_id)
        if shelf is not None:
            zones.add(shelf.zone_id)
    return zones


def zone_hops(world: WorldState, zone_a: str, zone_b: str) -> float:
    """Approximate number of storage-zone 'hops' between two zones (0 = same zone)."""
    if zone_a == zone_b:
        return 0.0
    a = world.zones.get(zone_a)
    b = world.zones.get(zone_b)
    if a is None or b is None:
        return 99.0
    unit_x = max(1, (a.x1 - a.x0 + 1) + 2)
    unit_y = max(1, (a.y1 - a.y0 + 1) + 2)
    ca, cb = a.center, b.center
    return abs(ca.x - cb.x) / unit_x + abs(ca.y - cb.y) / unit_y


def _compatible(world: WorldState, batch_zones: set[str], cand_zones: set[str], max_hops: float) -> bool:
    return all(min(zone_hops(world, z, s) for s in batch_zones) <= max_hops + 1e-9 for z in cand_zones)


def build_batches(
    world: WorldState,
    orders: list[Order],
    batch_max: int,
    capacity: int,
    max_hops: float = 1.0,
) -> list[list[Order]]:
    """Group ``orders`` (already sorted by urgency) into trips of at most ``batch_max`` orders."""
    if batch_max <= 1:
        return [[o] for o in orders]
    used: set[str] = set()
    zones_of = {o.id: order_zones(world, o) for o in orders}
    batches: list[list[Order]] = []
    for i, seed in enumerate(orders):
        if seed.id in used:
            continue
        used.add(seed.id)
        batch = [seed]
        items = seed.items
        batch_zones = set(zones_of[seed.id])
        if seed.priority == OrderPriority.CRITICAL:
            limit, hops = min(2, batch_max), 0.0
        elif seed.priority == OrderPriority.HIGH:
            limit, hops = batch_max, 0.0
        else:
            limit, hops = batch_max, max_hops
        if not batch_zones:
            batches.append(batch)
            continue
        for cand in orders[i + 1 :]:
            if len(batch) >= limit:
                break
            if cand.id in used or items + cand.items > capacity:
                continue
            cz = zones_of[cand.id]
            if not cz or not _compatible(world, batch_zones, cz, hops):
                continue
            batch.append(cand)
            used.add(cand.id)
            items += cand.items
            batch_zones |= cz
        batches.append(batch)
    return batches


def batch_summary(batches: list[list[Order]]) -> dict[str, float]:
    if not batches:
        return {"batches": 0, "orders": 0, "avg_orders_per_batch": 0.0, "max_orders_per_batch": 0}
    sizes = [len(b) for b in batches]
    return {
        "batches": len(batches),
        "orders": sum(sizes),
        "avg_orders_per_batch": round(sum(sizes) / len(sizes), 3),
        "max_orders_per_batch": max(sizes),
    }
