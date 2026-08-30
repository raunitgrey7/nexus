"""Battery exhaustion forecasting per robot.

For each operational robot::

    drain/min      = observed negative battery deltas over the history window (fallback: config-based
                     util · drain_move · speed · 60/ts + drain_idle · 60/ts)
    workload_drain = remaining_cells · drain_move + remaining_picks · pick_ticks · drain_action
    task_minutes   = (remaining_cells / speed + picks · pick_ticks + unload_ticks) · ts / 60

    exhaustion_min = task_minutes · battery / workload_drain              if battery ≤ workload_drain
                   = task_minutes + (battery − workload_drain) / drain_per_min   otherwise

``remaining_cells`` uses the same nearest-neighbour manhattan × detour estimate as task planning.
Charger ETA is the walking distance to the nearest enabled charger (exact BFS distance when a
:class:`~nexus.simulation.pathfinding.Pathfinder` is supplied, manhattan × detour otherwise).

Risk: ``high`` if exhaustion < charger_eta + 10 min, ``medium`` if < 25 min, else ``low``; a robot
below the engine's low-battery threshold that is not (heading to) charging is always ``high``.
"""

from __future__ import annotations

import itertools
from typing import TYPE_CHECKING

from nexus.api.schemas import BatteryForecast
from nexus.forecasting._common import ticks_to_minutes, world_utilization
from nexus.forecasting.history import HistoryRecorder
from nexus.simulation.tasks import DETOUR_FACTOR, estimate_cells
from nexus.twin.entities import Cell, ChargingStation, Robot, RobotStatus, TaskStatus, WaypointKind
from nexus.twin.world import WorldState

if TYPE_CHECKING:
    from nexus.simulation.pathfinding import Pathfinder

RISK_ORDER = {"high": 0, "medium": 1, "low": 2}
MAX_EXACT_REFINEMENTS = 24


def _nearest_charger(world: WorldState, cell: Cell) -> tuple[ChargingStation | None, float]:
    """Nearest enabled charger (prefer free slots) and its manhattan × detour distance in cells."""
    best: ChargingStation | None = None
    best_key: tuple[int, int, str] | None = None
    for c in world.chargers.values():
        if not c.enabled:
            continue
        key = (0 if c.free_slots > 0 else 1, c.cell.manhattan(cell), c.id)
        if best_key is None or key < best_key:
            best, best_key = c, key
    if best is None:
        return None, 0.0
    return best, best.cell.manhattan(cell) * DETOUR_FACTOR


def _drain_per_minute(world: WorldState, robot: Robot, history: HistoryRecorder | None, util: float) -> float:
    cfg = world.config
    ts = world.clock.tick_seconds
    if history is not None:
        series = history.robot_battery_series(robot.id)
        if len(series) >= 3:
            drop = 0.0
            for (_, a), (_, b) in itertools.pairwise(series):
                if b < a:
                    drop += a - b
            span_min = (series[-1][0] - series[0][0]) * ts / 60.0
            if span_min >= 2.0 and drop > 0:
                return drop / span_min
    return util * cfg.battery_drain_move * robot.speed * 60.0 / ts + cfg.battery_drain_idle * 60.0 / ts


def forecast_battery(
    world: WorldState,
    pathfinder: Pathfinder | None = None,
    history: HistoryRecorder | None = None,
) -> list[BatteryForecast]:
    cfg = world.config
    ts = world.clock.tick_seconds
    util = world_utilization(world)
    pending = len(world.pending_orders())
    operational = max(1, len(world.operational_robots()))
    queue_share = round(pending / operational)
    out: list[BatteryForecast] = []
    refinements = 0

    for rid in sorted(world.robots):
        r = world.robots[rid]
        status = r.status.value
        if not r.status.operational:
            if r.recover_at_tick is not None:
                left = ticks_to_minutes(world, max(0, r.recover_at_tick - world.clock.tick))
                rec = (
                    f"{rid} is offline ({r.failure_cause or 'unknown'}); recovery expected in {left:.0f} min"
                )
            else:
                rec = f"{rid} is offline ({r.failure_cause or 'unknown'}); no recovery scheduled"
            out.append(
                BatteryForecast(
                    robot_id=rid,
                    battery=round(r.battery, 1),
                    status=status,
                    workload_tasks=0,
                    predicted_exhaustion_min=None,
                    charger_eta_min=None,
                    risk="low",
                    recommendation=rec,
                )
            )
            continue
        if r.status == RobotStatus.CHARGING:
            to_target = max(0.0, cfg.battery_charge_target - r.battery) / max(1e-9, cfg.battery_charge_rate)
            mins = ticks_to_minutes(world, to_target)
            out.append(
                BatteryForecast(
                    robot_id=rid,
                    battery=round(r.battery, 1),
                    status=status,
                    workload_tasks=0,
                    predicted_exhaustion_min=None,
                    charger_eta_min=0.0,
                    risk="low",
                    recommendation=f"Charging at {r.charger_id or 'charger'} — {r.battery:.0f}% → {cfg.battery_charge_target:.0f}% in ~{mins:.0f} min",
                )
            )
            continue

        task = world.tasks.get(r.task_id) if r.task_id else None
        if task is not None and task.status != TaskStatus.ACTIVE:
            task = None
        drain_min = _drain_per_minute(world, r, history, util)
        remaining_cells = 0.0
        picks = 0
        unload = 0
        end_cell = r.cell
        if task is not None:
            remaining = task.remaining
            remaining_cells = float(estimate_cells(r.cell, remaining))
            picks = sum(1 for w in remaining if w.kind == WaypointKind.PICK)
            unload = sum(1 for w in remaining if w.kind == WaypointKind.DELIVER)
            if remaining:
                end_cell = remaining[-1].cell
        workload_drain = (
            remaining_cells * cfg.battery_drain_move + picks * cfg.pick_ticks * cfg.battery_drain_action
        )
        task_ticks = remaining_cells / max(1e-9, r.speed) + picks * cfg.pick_ticks + unload * cfg.unload_ticks
        task_min = task_ticks * ts / 60.0
        dies_in_task = task is not None and workload_drain > 0 and r.battery <= workload_drain
        if dies_in_task:
            exhaustion: float | None = task_min * r.battery / workload_drain
        elif drain_min > 1e-9:
            exhaustion = task_min + max(0.0, r.battery - workload_drain) / drain_min
        else:
            exhaustion = None

        # charger ETA: from the current cell if it would die mid-task, else from where the task ends
        origin = r.cell if (task is None or dies_in_task) else end_cell
        if r.status == RobotStatus.TO_CHARGER and r.charger_id in world.chargers:
            charger: ChargingStation | None = world.chargers[r.charger_id]
            dist = charger.cell.manhattan(r.cell) * DETOUR_FACTOR if charger else 0.0
        else:
            charger, dist = _nearest_charger(world, origin)
        if charger is not None and pathfinder is not None and refinements < MAX_EXACT_REFINEMENTS:
            provisional = exhaustion is not None and exhaustion < 40.0
            if provisional or r.battery < cfg.battery_low_threshold * 1.5:
                exact = pathfinder.distance(origin, charger.cell)
                if exact >= 0:
                    dist = float(exact)
                refinements += 1
        eta = ticks_to_minutes(world, dist / max(1e-9, r.speed)) if charger is not None else None

        # risk classification
        if charger is None:
            risk = "high"
        elif exhaustion is None:
            risk = "low"
        elif exhaustion < (eta or 0.0) + 10.0:
            risk = "high"
        elif exhaustion < 25.0:
            risk = "medium"
        else:
            risk = "low"
        if r.status == RobotStatus.TO_CHARGER and risk != "high":
            risk = "low"
        elif r.battery < cfg.battery_low_threshold and r.status != RobotStatus.TO_CHARGER:
            risk = "high"  # below the engine's own low-battery threshold and not charging

        eta_txt = f"{eta:.0f} min away" if eta is not None else "unreachable"
        cid = charger.id if charger is not None else "charger"
        ex_txt = f"{exhaustion:.0f} min" if exhaustion is not None else "n/a"
        if charger is None:
            rec = f"No enabled charger available for {rid} — enable a charging station"
        elif r.status == RobotStatus.TO_CHARGER:
            rec = f"Heading to {cid} (ETA {eta:.0f} min)" if eta is not None else f"Heading to {cid}"
        elif risk == "high" and dies_in_task:
            rec = f"Interrupt current task and charge {rid} now (predicted exhaustion in {ex_txt}, {cid} {eta_txt})"
        elif risk == "high" and task is not None:
            rec = f"Send {rid} to charging after current task (predicted exhaustion in {ex_txt}, {cid} {eta_txt})"
        elif risk == "high":
            rec = f"Send {rid} to charging now (predicted exhaustion in {ex_txt}, {cid} {eta_txt})"
        elif risk == "medium" and exhaustion is not None:
            window = max(1.0, exhaustion - (eta or 0.0) - 10.0)
            rec = f"Schedule charging for {rid} within {window:.0f} min (predicted exhaustion in {ex_txt})"
        elif exhaustion is not None:
            rec = f"Healthy — ~{exhaustion:.0f} min of autonomy at current workload"
        else:
            rec = "Healthy — no measurable drain"

        out.append(
            BatteryForecast(
                robot_id=rid,
                battery=round(r.battery, 1),
                status=status,
                workload_tasks=(1 if task is not None else 0) + queue_share,
                predicted_exhaustion_min=round(exhaustion, 1) if exhaustion is not None else None,
                charger_eta_min=round(eta, 1) if eta is not None else None,
                risk=risk,  # type: ignore[arg-type]
                recommendation=rec,
            )
        )

    out.sort(
        key=lambda b: (
            RISK_ORDER[b.risk],
            b.predicted_exhaustion_min if b.predicted_exhaustion_min is not None else float("inf"),
            b.robot_id,
        )
    )
    return out
