"""Bottleneck detection: rules over the world and the other forecasts, each with a concrete
recommendation. Severities are in [0, 1]; the list is sorted most severe first and capped.

Rules (severity):
* zone congestion — ``projected / (2 · capacity)`` for medium/high zones
* dock — closed dock 0.5; queue ≥ 3 → ``queue / 6``; no loader assigned 0.3
* charger — robots that need charging vs free slots: ``0.3 + 0.7 · (need − free) / need``
* robot — one per failed robot: ``0.3 + 3 / fleet_size``
* inventory — hot SKU (top 10 % popularity) with stock below the replenishment threshold
* worker — delayed / absent loader affecting a dock 0.4 (0.6 if the dock is already queued)
* demand — projected utilization > 0.9: ``(utilization − 0.7) / 0.5``; backlog age vs SLA
"""

from __future__ import annotations

import math

from nexus.api.schemas import BatteryForecast, Bottleneck, CongestionForecast, DemandForecast
from nexus.forecasting._common import clamp, ticks_to_minutes
from nexus.forecasting.demand import capacity_per_hour
from nexus.twin.entities import OrderStatus, RobotStatus, WorkerStatus, ZoneKind
from nexus.twin.world import WorldState

MAX_BOTTLENECKS = 12
HOT_SKU_SHARE = 0.10


def _least_loaded(
    world: WorldState, congestion: list[CongestionForecast], kind: ZoneKind, exclude: str
) -> str | None:
    best: tuple[float, str] | None = None
    for c in congestion:
        z = world.zones.get(c.zone_id)
        if z is None or z.kind != kind or z.closed or c.zone_id == exclude:
            continue
        key = (c.projected_robots / max(1, c.capacity), c.zone_id)
        if best is None or key < best:
            best = key
    return best[1] if best else None


def detect_bottlenecks(
    world: WorldState,
    demand: DemandForecast,
    battery: list[BatteryForecast] | None = None,
    congestion: list[CongestionForecast] | None = None,
    deep: bool = True,
) -> list[Bottleneck]:
    congestion = congestion or []
    cfg = world.config
    tick = world.clock.tick
    out: list[Bottleneck] = []

    # ---- zones ---------------------------------------------------------------------------------
    for c in congestion:
        if c.risk == "low":
            continue
        zone = world.zones.get(c.zone_id)
        if zone is None:
            continue
        severity = clamp(c.projected_robots / (2.0 * max(1, c.capacity)), 0.0, 1.0)
        if zone.kind == ZoneKind.CORRIDOR:
            alt = _least_loaded(world, congestion, ZoneKind.CORRIDOR, c.zone_id)
            rec = (
                f"Reroute traffic through {world.zones[alt].name}"
                if alt
                else "Stagger dispatch to spread corridor traffic"
            )
        else:
            alt = _least_loaded(world, congestion, ZoneKind.STORAGE, c.zone_id)
            rec = (
                f"Pre-position hot inventory from {zone.name} in {world.zones[alt].name} and stagger dispatch (prioritise HIGH/CRITICAL orders)"
                if alt
                else f"Stagger dispatch to {zone.name} and prioritise HIGH/CRITICAL orders"
            )
        when = "now" if c.eta_min <= 0 else f"in ~{c.eta_min:.0f} min"
        out.append(
            Bottleneck(
                kind="zone",
                entity_id=c.zone_id,
                severity=round(severity, 3),
                message=f"{zone.name}: projected {c.projected_robots:.1f} robots vs capacity {c.capacity} ({c.projected_change_pct:+.0f}%) {when}",
                recommendation=rec,
            )
        )

    # ---- docks + loaders ------------------------------------------------------------------------
    open_docks = [d for d in world.docks.values() if d.open]
    for dock in sorted(world.docks.values(), key=lambda d: d.id):
        if not dock.open:
            others = ", ".join(d.id for d in open_docks[:3]) or "none"
            out.append(
                Bottleneck(
                    kind="dock",
                    entity_id=dock.id,
                    severity=0.5,
                    message=f"Dock {dock.id} is closed; deliveries rebalance onto {others}",
                    recommendation=f"Reopen {dock.id} or spread deliveries across {others}",
                )
            )
            continue
        loaders = [w for w in world.workers.values() if w.role == "loader" and w.cell.x == dock.cell.x]
        active = [w for w in loaders if w.status in (WorkerStatus.AVAILABLE, WorkerStatus.BUSY)]
        queued = len(dock.queue)
        if queued >= 3:
            out.append(
                Bottleneck(
                    kind="dock",
                    entity_id=dock.id,
                    severity=round(clamp(queued / 6.0, 0.0, 1.0), 3),
                    message=f"Dock {dock.id} has {queued} robots queued",
                    recommendation=f"Dispatch an extra loader to {dock.id} and route new deliveries to the least-queued dock",
                )
            )
        if loaders and not active:
            w = loaders[0]
            left = (
                ticks_to_minutes(world, max(0, w.delay_until_tick - tick))
                if w.status == WorkerStatus.DELAYED
                else None
            )
            out.append(
                Bottleneck(
                    kind="worker",
                    entity_id=w.id,
                    severity=0.6 if queued else 0.4,
                    message=f"Loader {w.name} ({w.id}) is {w.status.value}{f' for {left:.0f} more min' if left else ''} — {dock.id} unloads {cfg.unload_no_loader_factor:.0f}× slower",
                    recommendation=f"Dispatch another loader to {dock.id} or route deliveries to a staffed dock",
                )
            )
        elif not loaders:
            out.append(
                Bottleneck(
                    kind="dock",
                    entity_id=dock.id,
                    severity=0.3,
                    message=f"Dock {dock.id} has no loader assigned ({cfg.unload_no_loader_factor:.0f}× slower unloads)",
                    recommendation=f"Assign a loader to {dock.id}",
                )
            )

    # ---- chargers -------------------------------------------------------------------------------
    need = [
        r
        for r in world.robots.values()
        if r.status.operational
        and r.status not in (RobotStatus.CHARGING, RobotStatus.TO_CHARGER)
        and r.battery < cfg.battery_low_threshold + 10.0
    ]
    free = sum(c.free_slots for c in world.chargers.values())
    disabled = [c.id for c in world.chargers.values() if not c.enabled]
    if need and len(need) > free:
        severity = clamp(0.3 + 0.7 * (len(need) - free) / len(need), 0.0, 1.0)
        entity = disabled[0] if disabled else (min(world.chargers) if world.chargers else "chargers")
        rec = (
            f"Re-enable {', '.join(disabled[:3])}"
            if disabled
            else "Stagger charging: send robots to charge after their current task, one at a time"
        )
        out.append(
            Bottleneck(
                kind="charger",
                entity_id=entity,
                severity=round(severity, 3),
                message=f"{len(need)} robots need charging soon ({', '.join(sorted(r.id for r in need)[:4])}) but only {free} charger slots are free"
                + (f"; {len(disabled)} stations disabled" if disabled else ""),
                recommendation=rec,
            )
        )
    elif disabled:
        out.append(
            Bottleneck(
                kind="charger",
                entity_id=disabled[0],
                severity=round(clamp(0.2 + 0.2 * len(disabled), 0.0, 1.0), 3),
                message=f"{len(disabled)} charging stations disabled ({', '.join(disabled[:3])}); {free} slots free",
                recommendation=f"Re-enable {disabled[0]} before the afternoon charging wave",
            )
        )

    # ---- failed robots --------------------------------------------------------------------------
    fleet = max(1, len(world.robots))
    healthy = sorted(r.id for r in world.robots.values() if r.status.operational and r.task_id is None)
    for r in sorted(world.failed_robots(), key=lambda r: r.id):
        since = ticks_to_minutes(world, tick - (r.failed_tick or tick))
        if r.recover_at_tick is not None:
            eta_txt = (
                f"recovery expected in {ticks_to_minutes(world, max(0, r.recover_at_tick - tick)):.0f} min"
            )
        else:
            eta_txt = "no recovery scheduled"
        cover = ", ".join(healthy[:2]) if healthy else "the remaining fleet"
        out.append(
            Bottleneck(
                kind="robot",
                entity_id=r.id,
                severity=round(clamp(0.3 + 3.0 / fleet, 0.0, 1.0), 3),
                message=f"{r.id} offline ({r.failure_cause or 'unknown'}) for {since:.0f} min; {eta_txt}",
                recommendation=f"Reassign {r.id}'s zone coverage to {cover} and simulate the reallocation before executing",
            )
        )

    # ---- hot-SKU stockouts ----------------------------------------------------------------------
    if deep and world.sku_popularity:
        ranked = sorted(world.sku_popularity.items(), key=lambda kv: (-kv[1], kv[0]))
        hot = ranked[: max(1, int(len(ranked) * HOT_SKU_SHARE))]
        found = 0
        for rank, (sku, _pop) in enumerate(hot, start=1):
            shelves = world.sku_index.get(sku, [])
            stock = sum(world.shelves[s].inventory.get(sku, 0) for s in shelves if s in world.shelves)
            if stock >= cfg.replenish_threshold:
                continue
            severity = clamp(0.4 + 0.6 * (1.0 - rank / max(1, len(hot))), 0.0, 1.0)
            where = ", ".join(shelves[:3]) or "no shelf"
            out.append(
                Bottleneck(
                    kind="inventory",
                    entity_id=sku,
                    severity=round(severity, 3),
                    message=f"{sku} (top-{rank} mover) has {stock} units left across {where}",
                    recommendation=f"Restock {sku} now — orders for it will stall until replenishment",
                )
            )
            found += 1
            if found >= 3:
                break

    # ---- demand vs capacity ---------------------------------------------------------------------
    if demand.projected_utilization > 0.9:
        capacity, effective = capacity_per_hour(world)
        per_robot = capacity / effective if effective > 0 else 0.0
        gap = demand.forecast_rate_per_hour - capacity
        extra = math.ceil(gap / per_robot) if per_robot > 0 and gap > 0 else 1
        batch = max(2, cfg.batch_max_orders + 1) if cfg.batch_max_orders < 3 else cfg.batch_max_orders
        out.append(
            Bottleneck(
                kind="demand",
                entity_id="capacity",
                severity=round(clamp((demand.projected_utilization - 0.7) / 0.5, 0.0, 1.0), 3),
                message=f"Forecast {demand.forecast_rate_per_hour:.0f} orders/h vs capacity {capacity:.0f} orders/h (projected utilization {demand.projected_utilization:.2f})",
                recommendation=f"+{max(1, extra)} robots or enable batching ({batch} orders/trip) to lift capacity to ~{capacity * batch / max(1.0, float(cfg.batch_max_orders)):.0f} orders/h",
            )
        )

    # ---- backlog age ----------------------------------------------------------------------------
    pending = [o for o in world.open_orders() if o.status == OrderStatus.PENDING]
    if pending:
        oldest = min(pending, key=lambda o: (o.created_tick, o.id))
        age = ticks_to_minutes(world, tick - oldest.created_tick)
        sla = ticks_to_minutes(world, oldest.deadline_tick - oldest.created_tick)
        if sla > 0 and age > 0.5 * sla:
            idle = [r for r in world.robots.values() if r.available]
            cause = (
                "idle robots exist — its lines are probably unsourceable (stock-out or closed zone)"
                if idle
                else "no free robots — add capacity or enable batching"
            )
            out.append(
                Bottleneck(
                    kind="demand",
                    entity_id="backlog",
                    severity=round(clamp(age / sla, 0.0, 1.0), 3),
                    message=f"{len(pending)} orders pending; oldest {oldest.id} has waited {age:.1f} of its {sla:.0f} min SLA",
                    recommendation=f"Prioritise {oldest.id}: {cause}",
                )
            )

    out.sort(key=lambda b: (-b.severity, b.kind, b.entity_id))
    return out[:MAX_BOTTLENECKS]
