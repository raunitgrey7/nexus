"""Zone congestion forecasting.

Mean zone occupancy follows Little's law (``L = λ·W``) and is small for every zone; congestion in a
warehouse is a *clustering* phenomenon — robots converge on the same aisles because the dispatcher
hands out the oldest orders to every idle robot in the same tick, and busy robots are already en
route. The forecast therefore estimates the **peak** concurrent occupancy over the next few minutes::

    projected = 0.5·inside + 0.9·en_route + 0.85·wave + stream + trend

    inside   robots in the zone now (about half leave within one dwell time)
    en_route robots whose remaining path or next pick waypoint lies in the zone
    wave     orders among the next |idle robots| pending orders (FIFO) that require the zone
             → dispatched simultaneously, arriving together
    stream   Little's-law mean concurrency of later dispatches: visits · dwell / horizon
    trend    recent occupancy slope from history × min(15, horizon) minutes (≥ 0)

``eta_min`` is the arrival time of the converging robots (0 if the zone is congested now, the
horizon if no crossing is expected). Risk: ``high`` if projected > capacity, ``medium`` if
≥ 0.75 · capacity, else ``low``.
"""

from __future__ import annotations

from collections import defaultdict

from nexus.api.schemas import CongestionForecast
from nexus.forecasting._common import (
    busy_robots,
    dispatchable_robots,
    mean_task_ticks,
    minutes_to_ticks,
    order_zones,
    pending_fifo,
    ticks_to_minutes,
)
from nexus.forecasting.history import HistoryRecorder
from nexus.forecasting.smoothing import linear_slope
from nexus.twin.entities import OrderStatus, TaskStatus, WaypointKind, Zone, ZoneKind
from nexus.twin.world import WorldState

RISK_ORDER = {"high": 0, "medium": 1, "low": 2}
INSIDE_W = 0.5
EN_ROUTE_W = 0.9
WAVE_W = 0.85


def _dwell_ticks(world: WorldState, zone: Zone) -> float:
    cfg = world.config
    if zone.kind == ZoneKind.CORRIDOR:
        return 15.0 / max(1e-9, cfg.robot_speed)
    width = zone.x1 - zone.x0 + 1
    height = zone.y1 - zone.y0 + 1
    return (width + height / 2.0) / max(1e-9, cfg.robot_speed) + 1.5 * cfg.pick_ticks


def _zone_of(world: WorldState, x: int, y: int) -> str | None:
    return world.grid.zone_of(x, y)


def forecast_congestion(
    world: WorldState,
    history: HistoryRecorder | None = None,
    horizon_min: int = 30,
) -> list[CongestionForecast]:
    horizon = max(1, int(horizon_min))
    horizon_ticks = max(1, minutes_to_ticks(world, horizon))
    zones = [z for z in world.zones.values() if z.kind in (ZoneKind.STORAGE, ZoneKind.CORRIDOR)]
    if not zones:
        return []
    speed = max(1e-9, world.config.robot_speed)
    task_ticks = mean_task_ticks(world)

    # ---- robots converging on zones (exact: remaining paths + next pick waypoint) ------------------
    en_route: dict[str, set[str]] = defaultdict(set)
    arrival_ticks: dict[str, list[float]] = defaultdict(list)
    for r in world.robots.values():
        if not r.status.operational:
            continue
        seen: set[str] = set()
        for i, cell in enumerate(r.path):
            z = _zone_of(world, cell.x, cell.y)
            if z is None or z == r.zone_id or z in seen:
                continue
            seen.add(z)
            en_route[z].add(r.id)
            arrival_ticks[z].append((i + 1) / r.speed)
        task = world.tasks.get(r.task_id) if r.task_id else None
        if task is not None and task.status == TaskStatus.ACTIVE and task.current is not None:
            wp = task.current
            if wp.kind == WaypointKind.PICK:
                z = _zone_of(world, wp.cell.x, wp.cell.y)
                if z is not None and z != r.zone_id and z not in seen:
                    en_route[z].add(r.id)
                    arrival_ticks[z].append(r.cell.manhattan(wp.cell) * 1.25 / r.speed)

    # ---- open-order demand per zone ------------------------------------------------------------
    open_by_zone: dict[str, int] = defaultdict(int)
    assigned_by_zone: dict[str, int] = defaultdict(int)
    for order in world.open_orders():
        for z in order_zones(world, order):
            open_by_zone[z] += 1
            if order.status != OrderStatus.PENDING:
                assigned_by_zone[z] += 1
    pending = pending_fifo(world)
    idle = dispatchable_robots(world)
    busy = busy_robots(world)
    wave_orders = pending[: len(idle)]
    dispatches = int(len(busy) * horizon_ticks / max(1.0, task_ticks))
    stream_orders = pending[len(idle) : len(idle) + dispatches]
    wave_by_zone: dict[str, int] = defaultdict(int)
    stream_by_zone: dict[str, int] = defaultdict(int)
    for order in wave_orders:
        for z in order_zones(world, order):
            wave_by_zone[z] += 1
    for order in stream_orders:
        for z in order_zones(world, order):
            stream_by_zone[z] += 1
    idle_cells = [r.cell for r in idle]

    # ---- spillover from closed storage zones ----------------------------------------------------
    closed_skus: dict[str, set[str]] = {}
    for closed_zone in zones:
        if closed_zone.closed and closed_zone.kind == ZoneKind.STORAGE:
            closed_skus[closed_zone.id] = {
                sku for s in world.shelves_in_zone(closed_zone.id) for sku in s.inventory
            }
    adjacency = world.zone_adjacency()

    out: list[CongestionForecast] = []
    for zone in zones:
        zid = zone.id
        inside = world.zone_occupancy.get(zid, 0)
        routed = len(en_route.get(zid, ()))
        wave = wave_by_zone.get(zid, 0)
        stream_visits = stream_by_zone.get(zid, 0)
        dwell = _dwell_ticks(world, zone)
        stream = stream_visits * dwell / horizon_ticks
        trend = 0.0
        slope = 0.0
        if history is not None:
            series = history.zone_series(zid, window_min=15)
            if len(series) >= 3:
                xs = [ticks_to_minutes(world, t - series[0][0]) for t, _ in series]
                slope = linear_slope([float(v) for _, v in series], xs)
                trend = max(0.0, slope * min(15.0, float(horizon)))
        projected = INSIDE_W * inside + EN_ROUTE_W * routed + WAVE_W * wave + stream + trend
        projected = min(projected, float(len(world.robots)))
        capacity = max(1, zone.capacity)

        # eta: converging robots first, then trend crossing, else the horizon
        if inside > capacity:
            eta = 0.0
        elif projected > capacity:
            candidates = list(arrival_ticks.get(zid, []))
            if wave and idle_cells:
                center = zone.center
                mean_dist = sum(c.manhattan(center) for c in idle_cells) / len(idle_cells) * 1.25
                candidates.append(mean_dist / speed)
            if candidates:
                candidates.sort()
                eta = ticks_to_minutes(world, candidates[len(candidates) // 2])
            elif slope > 1e-9:
                eta = min(float(horizon), (capacity - inside) / slope)
            else:
                eta = float(horizon)
        else:
            eta = float(horizon)

        if projected > capacity or inside > capacity:
            risk = "high"
        elif projected >= 0.75 * capacity:
            risk = "medium"
        else:
            risk = "low"

        drivers: list[str] = []
        open_here = open_by_zone.get(zid, 0)
        if open_here:
            drivers.append(
                f"{open_here} open orders require {zone.name} ({wave} dispatching now, {assigned_by_zone.get(zid, 0)} assigned)"
            )
        if routed:
            drivers.append(f"{routed} robots en route ({', '.join(sorted(en_route[zid])[:4])})")
        if inside:
            drivers.append(f"{inside} robots inside now (capacity {capacity})")
        if slope > 0.05:
            drivers.append(f"occupancy rising +{slope:.2f} robots/min over the last 15 min")
        for other in sorted(adjacency.get(zid, ())):
            oz = world.zones.get(other)
            if oz is None or oz.kind != ZoneKind.CORRIDOR:
                continue
            if world.zone_occupancy.get(other, 0) > oz.capacity:
                drivers.append(
                    f"adjacent {oz.name} congested ({world.zone_occupancy.get(other, 0)}/{oz.capacity})"
                )
        if closed_skus and zone.kind == ZoneKind.STORAGE and not zone.closed:
            for closed_id, skus in closed_skus.items():
                n = sum(1 for s in world.shelves_in_zone(zid) if any(sku in skus for sku in s.inventory))
                if n:
                    drivers.append(
                        f"spillover: {n} shelves here back up SKUs from closed {world.zones[closed_id].name}"
                    )
        if zone.closed:
            drivers.append("zone closed")
        if not drivers:
            drivers.append("no inbound demand")

        out.append(
            CongestionForecast(
                zone_id=zid,
                zone_name=zone.name,
                robots_now=inside,
                capacity=capacity,
                projected_robots=round(projected, 2),
                projected_change_pct=round((projected - inside) / max(1, inside) * 100.0, 1),
                eta_min=round(eta, 1),
                risk=risk,  # type: ignore[arg-type]
                drivers=drivers[:6],
            )
        )
    out.sort(key=lambda c: (RISK_ORDER[c.risk], -c.projected_robots / max(1, c.capacity), c.zone_id))
    return out
