"""Causal attribution of delay: which factors are slowing orders down, and by how much.

The attribution is a transparent, weighted decomposition over observable causes (congested zones,
unavailable robots, charging, backlog without free robots, closed/blocked infrastructure, delayed
workers). Weights are proportional to the open orders each cause is currently holding back, so the
shares sum to 100% and every number is traceable to entities in the twin.
"""

from __future__ import annotations

from typing import Any

from nexus.simulation.metrics import compute_kpis
from nexus.twin.entities import OrderStatus, RobotStatus, WorkerStatus, ZoneKind
from nexus.twin.world import WorldState


def attribute_delay(world: WorldState) -> dict[str, Any]:
    kpis = compute_kpis(world)
    open_orders = world.open_orders()
    tick = world.clock.tick
    zone_orders: dict[str, int] = {}
    for o in open_orders:
        for z in {
            world.shelves[ln.shelf_id].zone_id
            for ln in o.lines
            if ln.shelf_id in world.shelves and not ln.picked
        }:
            zone_orders[z] = zone_orders.get(z, 0) + 1
    causes: list[dict[str, Any]] = []

    # 1. congestion per zone: excess robots × orders needing the zone
    for zone in world.zones.values():
        if zone.kind not in (ZoneKind.STORAGE, ZoneKind.CORRIDOR):
            continue
        occ = world.zone_occupancy.get(zone.id, 0)
        excess = occ - zone.capacity
        if excess > 0:
            weight = (
                excess * max(1, zone_orders.get(zone.id, 1)) * (1.0 if zone.kind == ZoneKind.STORAGE else 0.6)
            )
            causes.append(
                {
                    "cause": "zone_congestion",
                    "entity": zone.id,
                    "weight": weight,
                    "detail": f"{zone.name}: {occ}/{zone.capacity} robots, {zone_orders.get(zone.id, 0)} open orders need it",
                }
            )
    # 2. unavailable robots
    failed = [r for r in world.robots.values() if not r.status.operational]
    if failed:
        share = len(failed) / max(1, len(world.robots))
        causes.append(
            {
                "cause": "robot_unavailable",
                "entity": ",".join(r.id for r in failed),
                "weight": share * len(open_orders) * 1.2,
                "detail": f"{len(failed)} robot(s) offline: "
                + ", ".join(f"{r.id} ({r.failure_cause})" for r in failed),
            }
        )
    # 3. charging
    charging = [
        r for r in world.robots.values() if r.status in (RobotStatus.CHARGING, RobotStatus.TO_CHARGER)
    ]
    if charging:
        causes.append(
            {
                "cause": "charging",
                "entity": ",".join(r.id for r in charging),
                "weight": len(charging) / max(1, len(world.robots)) * len(open_orders) * 0.6,
                "detail": f"{len(charging)} robot(s) charging",
            }
        )
    # 4. backlog without free robots
    pending = [o for o in open_orders if o.status == OrderStatus.PENDING]
    idle = [
        r for r in world.robots.values() if r.available and r.battery >= world.config.battery_low_threshold
    ]
    if pending and not idle:
        oldest = max(tick - o.created_tick for o in pending) * world.clock.tick_seconds / 60
        causes.append(
            {
                "cause": "capacity_backlog",
                "entity": "fleet",
                "weight": len(pending) * 0.8,
                "detail": f"{len(pending)} orders waiting for a free robot (oldest {oldest:.0f} min)",
            }
        )
    # 5. infrastructure
    if world.grid.closed_zones:
        affected = sum(zone_orders.get(z, 0) for z in world.grid.closed_zones)
        causes.append(
            {
                "cause": "zone_closed",
                "entity": ",".join(sorted(world.grid.closed_zones)),
                "weight": affected * 1.5 + 1,
                "detail": f"closed zones {sorted(world.grid.closed_zones)} hold {affected} open orders",
            }
        )
    if world.grid.blocked:
        causes.append(
            {
                "cause": "aisle_blocked",
                "entity": "grid",
                "weight": len(world.grid.blocked) * 0.5 + world.stats.replans_total * 0.02,
                "detail": f"{len(world.grid.blocked)} blocked cells forcing detours ({world.stats.replans_total} replans so far)",
            }
        )
    closed_docks = [d for d in world.docks.values() if not d.open]
    if closed_docks:
        causes.append(
            {
                "cause": "dock_closed",
                "entity": ",".join(d.id for d in closed_docks),
                "weight": len(closed_docks) / max(1, len(world.docks)) * len(open_orders) * 0.5,
                "detail": f"{len(closed_docks)} dock(s) closed",
            }
        )
    delayed = [
        w
        for w in world.workers.values()
        if w.status in (WorkerStatus.DELAYED, WorkerStatus.ABSENT, WorkerStatus.BREAK) and w.role == "loader"
    ]
    if delayed:
        causes.append(
            {
                "cause": "worker_delay",
                "entity": ",".join(w.id for w in delayed),
                "weight": len(delayed) * 2.0,
                "detail": f"{len(delayed)} loader(s) unavailable — unloading takes 2× longer",
            }
        )
    # 6. demand pressure
    rate = (
        world.demand.rate_per_tick(world.clock.hour_of_day(), tick, world.clock.tick_seconds)
        * 3600
        / world.clock.tick_seconds
    )
    if kpis.robot_utilization > 0.85:
        causes.append(
            {
                "cause": "demand_pressure",
                "entity": "demand",
                "weight": (kpis.robot_utilization - 0.85) * 40 + 1,
                "detail": f"utilization {kpis.robot_utilization:.0%} at {rate:.0f} orders/h",
            }
        )

    total = sum(c["weight"] for c in causes)
    if total <= 0:
        causes = [
            {"cause": "none", "entity": "-", "weight": 1.0, "detail": "no material delay driver detected"}
        ]
        total = 1.0
    for c in causes:
        c["share"] = round(c["weight"] / total, 4)
        del c["weight"]
    causes.sort(key=lambda c: -c["share"])
    return {
        "tick": tick,
        "kpis": kpis.headline()
        | {
            "p95_fulfillment_min": kpis.p95_fulfillment_min,
            "orders_open": kpis.orders_open,
            "orders_pending": kpis.orders_pending,
        },
        "causes": causes,
        "primary": causes[0],
    }


CAUSE_LABEL = {
    "zone_congestion": "Zone {entity} congestion",
    "robot_unavailable": "Robot unavailability ({entity})",
    "charging": "Robots charging ({entity})",
    "capacity_backlog": "Fleet capacity backlog",
    "zone_closed": "Closed zone(s) {entity}",
    "aisle_blocked": "Blocked aisle(s)",
    "dock_closed": "Closed dock(s) {entity}",
    "worker_delay": "Loader delays ({entity})",
    "demand_pressure": "Demand pressure",
    "none": "No material driver",
}


def explain_text(attribution: dict[str, Any]) -> str:
    k = attribution["kpis"]
    primary = attribution["primary"]
    label = CAUSE_LABEL.get(primary["cause"], primary["cause"]).format(entity=primary["entity"])
    if primary["cause"] == "none":
        return f"Orders are flowing normally: projected SLA breach {k['sla_breach_rate_projected']:.1%}, average fulfillment {k['avg_fulfillment_min']:.1f} min, utilization {k['robot_utilization']:.0%}."
    parts = [
        f"{label} is currently the largest contributor, accounting for approximately {primary['share']:.0%} of predicted delay ({primary['detail']})."
    ]
    others = attribution["causes"][1:3]
    if others:
        parts.append(
            "Other contributors: "
            + "; ".join(
                f"{CAUSE_LABEL.get(c['cause'], c['cause']).format(entity=c['entity'])} {c['share']:.0%}"
                for c in others
            )
            + "."
        )
    parts.append(
        f"Right now: {k['orders_open']} open orders ({k['orders_pending']} pending), projected SLA breach {k['sla_breach_rate_projected']:.1%}, p95 fulfillment {k['p95_fulfillment_min']:.1f} min, utilization {k['robot_utilization']:.0%}."
    )
    return " ".join(parts)
