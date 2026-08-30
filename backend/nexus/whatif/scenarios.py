"""Scenario DSL → scheduled faults.

A scenario is a list of typed mutations applied to a forked world at ``start + at_min``. Mutations
become ordinary external events, so the same replay/audit machinery covers what-if runs.
"""

from __future__ import annotations

from typing import Any

from nexus.api.schemas import MutationModel, ScenarioModel
from nexus.events.types import EventType
from nexus.simulation.faults import ScheduledFault
from nexus.twin.entities import Cell
from nexus.twin.world import WorldState


def _minutes(world: WorldState, minutes: float) -> int:
    return int(minutes * 60 / world.clock.tick_seconds)


def mutation_faults(world: WorldState, m: MutationModel, start_tick: int, index: int) -> list[ScheduledFault]:
    tick = start_tick + _minutes(world, m.at_min)
    p: dict[str, Any] = dict(m.params)
    key = f"scenario:{index}:{m.type}"
    out: list[ScheduledFault] = []

    def add(type_: EventType, entity: str | None, payload: dict[str, Any], sub: str = "") -> None:
        out.append(ScheduledFault(tick, type_, entity, payload, f"{key}:{sub or entity or '-'}", "scenario"))

    if m.type == "ROBOT_FAILURE":
        ids = p.get("robot_ids") or (
            [p["robot_id"]]
            if p.get("robot_id")
            else ["R07" if "R07" in world.robots else next(iter(sorted(world.robots)))]
        )
        recovery = _minutes(world, float(p.get("recovery_min", 45)))
        for rid in ids:
            if rid in world.robots:
                add(
                    EventType.ROBOT_FAILURE,
                    rid,
                    {"cause": p.get("cause", "motor_fault"), "recovery_ticks": recovery},
                )
    elif m.type == "REMOVE_ROBOTS":
        ids = (
            p.get("robot_ids")
            or sorted((r.id for r in world.robots.values() if r.status.operational), reverse=True)[
                : int(p.get("count", 2))
            ]
        )
        for rid in ids:
            add(EventType.ROBOT_REMOVED, rid, {"reason": "scenario"})
    elif m.type == "ADD_ROBOTS":
        bay = world.zones.get("CHG")
        bay_cells = (
            [Cell(x, y) for y in range(bay.y0, bay.y1 + 1) for x in range(bay.x0 + 1, bay.x1 + 1)]
            if bay
            else [r.cell for r in world.robots.values()]
        )
        n = len(world.robots)
        for k in range(int(p.get("count", 2))):
            rid = f"R{n + k + 1:02d}"
            cell = bay_cells[(n + k) % len(bay_cells)]
            add(
                EventType.ROBOT_ADDED,
                rid,
                {
                    "robot": {
                        "id": rid,
                        "cell": list(cell),
                        "zone_id": world.zone_at(cell) or "CHG",
                        "battery": 100.0,
                    }
                },
                rid,
            )
    elif m.type == "DEMAND_MULTIPLIER":
        add(EventType.DEMAND_CHANGED, None, {"multiplier": float(p.get("multiplier", 1.4))}, "mult")
    elif m.type == "DEMAND_BURST":
        add(
            EventType.DEMAND_CHANGED,
            None,
            {
                "burst_multiplier": float(p.get("multiplier", 2.0)),
                "burst_ticks": _minutes(world, float(p.get("duration_min", 30))),
            },
            "burst",
        )
    elif m.type == "CLOSE_ZONE":
        zid = p.get("zone_id", "B")
        if zid in world.zones:
            add(EventType.ZONE_CLOSED, zid, {"reason": p.get("reason", "scenario")})
            if p.get("reopen_min"):
                out.append(
                    ScheduledFault(
                        tick + _minutes(world, float(p["reopen_min"])),
                        EventType.ZONE_OPENED,
                        zid,
                        {},
                        f"{key}:reopen",
                        "scenario",
                    )
                )
    elif m.type == "CLOSE_DOCK":
        did = p.get("dock_id", "D2")
        if did in world.docks:
            add(EventType.DOCK_CLOSED, did, {"reason": p.get("reason", "scenario")})
    elif m.type == "DISABLE_CHARGERS":
        ids = (
            p.get("charger_ids")
            or sorted(c.id for c in world.chargers.values() if c.enabled)[
                : int(p.get("count", max(1, len(world.chargers) // 2)))
            ]
        )
        for cid in ids:
            add(EventType.CHARGER_DISABLED, cid, {"reason": "scenario"})
    elif m.type == "BLOCK_AISLE":
        cells: list[list[int]] | None = p.get("cells")
        if not cells and p.get("zone_id") in world.zones:
            z = world.zones[p["zone_id"]]
            aisles = int(p.get("aisles", 1))
            cells = []
            for a in range(aisles):
                x = z.x0 + 3 + 3 * a
                if x > z.x1:
                    break
                cells.extend([[x, y] for y in range(z.y0 + 1, z.y1)])
        if cells:
            add(
                EventType.AISLE_BLOCKED,
                None,
                {"cells": cells, "reason": p.get("reason", "scenario")},
                "block",
            )
            if p.get("clear_min"):
                out.append(
                    ScheduledFault(
                        tick + _minutes(world, float(p["clear_min"])),
                        EventType.AISLE_CLEARED,
                        None,
                        {"cells": cells},
                        f"{key}:clear",
                        "scenario",
                    )
                )
    elif m.type == "MOVE_INVENTORY":
        from nexus.optimization.engine import OptimizationEngine

        events = OptimizationEngine(world).reposition_inventory_events(
            p.get("from_zone", "C"), p.get("to_zone", "B"), int(p.get("skus", 6)), int(p.get("units", 40))
        )
        for k, (etype, entity, payload) in enumerate(events):
            add(etype, entity, payload, f"mv{k}")
    elif m.type == "WORKER_DELAY":
        ids = p.get("worker_ids") or sorted(world.workers)[:1]
        for wid in ids:
            if wid in world.workers:
                add(EventType.WORKER_DELAY, wid, {"ticks": _minutes(world, float(p.get("minutes", 30)))})
    elif m.type == "SET_SLA":
        sla = {k: float(v) for k, v in p.items() if k in ("LOW", "NORMAL", "HIGH", "CRITICAL")}
        if sla:
            add(EventType.CONFIG_CHANGED, None, {"sla_minutes": sla}, "sla")
    elif m.type == "SET_BATCHING":
        add(EventType.CONFIG_CHANGED, None, {"batch_max_orders": int(p.get("orders_per_trip", 3))}, "batch")
    return out


def scenario_faults(world: WorldState, scenario: ScenarioModel, start_tick: int) -> list[ScheduledFault]:
    faults: list[ScheduledFault] = []
    for i, m in enumerate(scenario.mutations):
        faults.extend(mutation_faults(world, m, start_tick, i))
    faults.sort(key=lambda f: f.tick)
    return faults


def describe_scenario(scenario: ScenarioModel) -> str:
    parts = []
    for m in scenario.mutations:
        p = m.params
        when = f" at +{m.at_min:.0f} min" if m.at_min else ""
        if m.type == "ROBOT_FAILURE":
            parts.append(f"{', '.join(p.get('robot_ids') or [p.get('robot_id', 'R07')])} fails{when}")
        elif m.type == "REMOVE_ROBOTS":
            parts.append(f"remove {p.get('count', len(p.get('robot_ids', [])))} robots{when}")
        elif m.type == "ADD_ROBOTS":
            parts.append(f"add {p.get('count', 2)} robots{when}")
        elif m.type == "DEMAND_MULTIPLIER":
            parts.append(f"demand ×{float(p.get('multiplier', 1.4)):.2f}{when}")
        elif m.type == "DEMAND_BURST":
            parts.append(
                f"demand burst ×{float(p.get('multiplier', 2.0)):.1f} for {p.get('duration_min', 30)} min{when}"
            )
        elif m.type == "CLOSE_ZONE":
            parts.append(f"zone {p.get('zone_id', 'B')} inaccessible{when}")
        elif m.type == "CLOSE_DOCK":
            parts.append(f"dock {p.get('dock_id', 'D2')} closed{when}")
        elif m.type == "DISABLE_CHARGERS":
            parts.append(f"{p.get('count', 'some')} chargers disabled{when}")
        elif m.type == "BLOCK_AISLE":
            parts.append(f"aisle blocked in zone {p.get('zone_id', '?')}{when}")
        elif m.type == "MOVE_INVENTORY":
            parts.append(
                f"move {p.get('skus', 6)} hot SKUs {p.get('from_zone', 'C')}→{p.get('to_zone', 'B')}{when}"
            )
        elif m.type == "WORKER_DELAY":
            parts.append(f"worker delay {p.get('minutes', 30)} min{when}")
        elif m.type == "SET_SLA":
            parts.append("tighter SLAs" + when)
        elif m.type == "SET_BATCHING":
            parts.append(f"batching {p.get('orders_per_trip', 3)}/trip{when}")
    return "; ".join(parts) or "no changes"
