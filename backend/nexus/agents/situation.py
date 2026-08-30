"""Situation analysis: a compact, structured picture of "what is happening" for planners and humans."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from nexus.events.types import NOTABLE_TYPES
from nexus.simulation.metrics import KPIs, compute_kpis
from nexus.twin.entities import OrderStatus, RobotStatus, ZoneKind
from nexus.twin.world import WorldState

if TYPE_CHECKING:
    from nexus.api.schemas import Forecast
    from nexus.simulation.engine import SimulationEngine


@dataclass(slots=True)
class Situation:
    tick: int
    sim_time: str
    kpis: KPIs
    fleet: dict[str, int]
    failed_robots: list[dict[str, Any]]
    low_battery: list[dict[str, Any]]
    congested_zones: list[dict[str, Any]]
    zone_load: list[dict[str, Any]]
    closed_zones: list[str]
    closed_docks: list[str]
    disabled_chargers: list[str]
    blocked_cells: int
    backlog: int
    oldest_pending_min: float
    open_orders_by_zone: dict[str, int]
    hot_zones: list[str]
    demand: dict[str, float]
    strategy: str
    batch_max: int
    recent_events: list[dict[str, Any]]
    forecast_summary: str = ""
    bottlenecks: list[dict[str, Any]] = field(default_factory=list)
    adjacency: dict[str, list[str]] = field(default_factory=dict)

    # ---- views -------------------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "tick": self.tick,
            "sim_time": self.sim_time,
            "kpis": self.kpis.headline()
            | {"orders_open": self.kpis.orders_open, "orders_pending": self.kpis.orders_pending},
            "fleet": self.fleet,
            "failed_robots": self.failed_robots,
            "low_battery": self.low_battery,
            "congested_zones": self.congested_zones,
            "closed_zones": self.closed_zones,
            "closed_docks": self.closed_docks,
            "disabled_chargers": self.disabled_chargers,
            "blocked_cells": self.blocked_cells,
            "backlog": self.backlog,
            "oldest_pending_min": self.oldest_pending_min,
            "open_orders_by_zone": self.open_orders_by_zone,
            "hot_zones": self.hot_zones,
            "demand": self.demand,
            "strategy": self.strategy,
            "batch_max": self.batch_max,
            "forecast_summary": self.forecast_summary,
            "bottlenecks": self.bottlenecks,
            "recent_events": self.recent_events[-8:],
            "summary": self.text(short=True),
        }

    def text(self, short: bool = False) -> str:
        k = self.kpis
        lines = [
            f"Time {self.sim_time[11:16]} (tick {self.tick}). Fleet: {self.fleet['operational']}/{self.fleet['total']} robots operational, "
            f"{self.fleet['busy']} busy, {self.fleet['idle']} idle, {self.fleet['charging']} charging.",
            f"Orders: {k.orders_open} open ({k.orders_pending} pending, backlog age {self.oldest_pending_min:.0f} min), "
            f"projected SLA breach {k.sla_breach_rate_projected:.1%}, avg fulfillment {k.avg_fulfillment_min:.1f} min, "
            f"throughput {k.throughput_per_hour:.0f}/h, utilization {k.robot_utilization:.0%}, congestion index {k.congestion_index:.2f}.",
            f"Demand: {self.demand['rate_per_hour']:.0f} orders/h now (multiplier {self.demand['multiplier']:.2f}). Strategy: {self.strategy}, batching {self.batch_max}/trip.",
        ]
        if self.failed_robots:
            lines.append(
                "Failed robots: "
                + ", ".join(
                    f"{r['id']} ({r['cause']}, zone {r['zone']}, {r['released_orders']} orders released)"
                    for r in self.failed_robots
                )
                + "."
            )
        if self.congested_zones:
            lines.append(
                "Congested zones: "
                + ", ".join(
                    f"{z['zone']} {z['robots']}/{z['capacity']} robots, {z['open_orders']} open orders need it"
                    for z in self.congested_zones
                )
                + "."
            )
        if self.hot_zones:
            lines.append(
                "Hottest zones by open demand: "
                + ", ".join(f"{z} ({self.open_orders_by_zone[z]})" for z in self.hot_zones)
                + "."
            )
        if self.low_battery:
            lines.append(
                "Low battery: " + ", ".join(f"{r['id']} {r['battery']:.0f}%" for r in self.low_battery) + "."
            )
        if self.closed_zones or self.closed_docks or self.disabled_chargers or self.blocked_cells:
            lines.append(
                f"Infrastructure: closed zones {self.closed_zones or 'none'}, closed docks {self.closed_docks or 'none'}, "
                f"disabled chargers {self.disabled_chargers or 'none'}, blocked cells {self.blocked_cells}."
            )
        if not short:
            if self.adjacency:
                lines.append(
                    "Zone adjacency: "
                    + "; ".join(f"{z}: {', '.join(n)}" for z, n in list(self.adjacency.items())[:14])
                    + "."
                )
            if self.bottlenecks:
                lines.append(
                    "Bottlenecks: "
                    + " | ".join(
                        f"{b['kind']} {b['entity_id']} ({b['severity']:.2f}): {b['message']}"
                        for b in self.bottlenecks[:5]
                    )
                )
            if self.recent_events:
                lines.append(
                    "Recent events: "
                    + "; ".join(
                        f"t{e['tick']} {e['type']} {e.get('entity_id') or ''}"
                        for e in self.recent_events[-6:]
                    )
                )
        return "\n".join(lines)


def analyze(
    world: WorldState, engine: SimulationEngine | None = None, forecast: Forecast | None = None
) -> Situation:
    kpis = compute_kpis(world)
    tick = world.clock.tick
    robots = list(world.robots.values())
    fleet = {
        "total": len(robots),
        "operational": sum(1 for r in robots if r.status.operational),
        "failed": sum(1 for r in robots if r.status == RobotStatus.FAILED),
        "busy": sum(1 for r in robots if r.task_id),
        "idle": sum(1 for r in robots if r.status == RobotStatus.IDLE),
        "charging": sum(1 for r in robots if r.status in (RobotStatus.CHARGING, RobotStatus.TO_CHARGER)),
    }
    open_by_zone: dict[str, int] = {}
    pending_ages: list[int] = []
    for order in world.open_orders():
        if order.status == OrderStatus.PENDING:
            pending_ages.append(tick - order.created_tick)
        zones = {
            world.shelves[line.shelf_id].zone_id
            for line in order.lines
            if line.shelf_id in world.shelves and not line.picked
        }
        for z in zones:
            open_by_zone[z] = open_by_zone.get(z, 0) + 1
    failed = []
    for r in robots:
        if r.status == RobotStatus.FAILED:
            released = sum(
                1
                for o in world.orders.values()
                if o.status == OrderStatus.PENDING
                and o.robot_id is None
                and o.task_id is None
                and (o.started_tick or 0) > 0
            )
            failed.append(
                {
                    "id": r.id,
                    "cause": r.failure_cause,
                    "zone": r.zone_id,
                    "failed_tick": r.failed_tick,
                    "recover_at_tick": r.recover_at_tick,
                    "released_orders": released,
                }
            )
    threshold = world.config.battery_low_threshold + 10
    low_batt = [
        {"id": r.id, "battery": round(r.battery, 1), "status": r.status.value, "zone": r.zone_id}
        for r in robots
        if r.status.operational and r.battery < threshold
    ]
    low_batt.sort(key=lambda x: x["battery"])
    congested = []
    zone_load = []
    for zone in world.zones.values():
        if zone.kind not in (ZoneKind.STORAGE, ZoneKind.CORRIDOR):
            continue
        occ = world.zone_occupancy.get(zone.id, 0)
        entry: dict[str, Any] = {
            "zone": zone.id,
            "name": zone.name,
            "kind": zone.kind.value,
            "robots": occ,
            "capacity": zone.capacity,
            "open_orders": open_by_zone.get(zone.id, 0),
            "closed": zone.closed,
        }
        zone_load.append(entry)
        if occ > zone.capacity:
            congested.append(entry)
    congested.sort(key=lambda e: (-(e["robots"] - e["capacity"]), e["zone"]))
    hot = sorted(open_by_zone, key=lambda z: (-open_by_zone[z], z))[:4]
    events: list[dict[str, Any]] = []
    if engine is not None:
        for ev in engine.store.recent_events(limit=12, types=NOTABLE_TYPES):
            events.append(
                {
                    "tick": ev.tick,
                    "type": ev.type.value,
                    "entity_id": ev.entity_id,
                    "payload": {
                        k: v
                        for k, v in ev.payload.items()
                        if k in ("cause", "reason", "multiplier", "plan_id")
                    },
                }
            )
    adjacency = {
        z: sorted(n) for z, n in world.zone_adjacency().items() if world.zones[z].kind == ZoneKind.STORAGE
    }
    rate = (
        world.demand.rate_per_tick(world.clock.hour_of_day(), tick, world.clock.tick_seconds)
        * 3600
        / world.clock.tick_seconds
    )
    strategy_name = getattr(engine.strategy, "name", "unknown") if engine is not None else "unknown"
    situation = Situation(
        tick=tick,
        sim_time=world.clock.now().isoformat(),
        kpis=kpis,
        fleet=fleet,
        failed_robots=failed,
        low_battery=low_batt[:6],
        congested_zones=congested[:6],
        zone_load=zone_load,
        closed_zones=sorted(world.grid.closed_zones),
        closed_docks=sorted(d.id for d in world.docks.values() if not d.open),
        disabled_chargers=sorted(c.id for c in world.chargers.values() if not c.enabled),
        blocked_cells=len(world.grid.blocked),
        backlog=kpis.orders_pending,
        oldest_pending_min=round(max(pending_ages) * world.clock.tick_seconds / 60.0, 1)
        if pending_ages
        else 0.0,
        open_orders_by_zone=dict(sorted(open_by_zone.items(), key=lambda kv: (-kv[1], kv[0]))),
        hot_zones=hot,
        demand={
            "rate_per_hour": round(rate, 1),
            "multiplier": world.demand.multiplier,
            "burst_until_tick": float(world.demand.burst_until_tick),
        },
        strategy=strategy_name,
        batch_max=int(getattr(engine.strategy, "batch_max", world.config.batch_max_orders))
        if engine is not None
        else world.config.batch_max_orders,
        recent_events=events,
        adjacency=adjacency,
    )
    if forecast is not None:
        situation.forecast_summary = forecast.summary
        situation.bottlenecks = [b.model_dump() for b in forecast.bottlenecks[:6]]
    return situation
