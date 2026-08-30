"""Constraint validator: rejects or sanitises plan actions before anything is optimized or simulated.

Checks are structural (ids exist, ranges are sane) and policy-level (never close the last dock, never
disable every charger, never remove more than a third of the fleet, never route everything through a
closed zone). Invalid actions are dropped; a plan with no remaining actions except NOOP is infeasible.
"""

from __future__ import annotations

from nexus.api.schemas import ActionModel, PlanModel
from nexus.twin.entities import RobotStatus
from nexus.twin.world import WorldState

PRIORITIES = {"LOW", "NORMAL", "HIGH", "CRITICAL"}


def _clamp(v: object, lo: float, hi: float, default: float) -> float:
    try:
        x = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, x))


def validate_action(world: WorldState, action: ActionModel, errors: list[str]) -> ActionModel | None:
    p = dict(action.params)
    t = action.type
    zones = world.zones
    robots = world.robots
    if t == "NOOP":
        return action
    if t == "REASSIGN_TASKS":
        to = [r for r in p.get("to_robots", []) if r in robots and robots[r].status.operational]
        frm = [r for r in p.get("from_robots", []) if r in robots]
        zs = [z for z in p.get("zones", []) if z in zones]
        if not to and not frm and not zs:
            errors.append("REASSIGN_TASKS: no valid robots or zones")
            return None
        p.update(
            {
                "to_robots": to,
                "from_robots": frm,
                "zones": zs,
                "max_tasks": int(_clamp(p.get("max_tasks", 12), 1, 60, 12)),
            }
        )
    elif t == "REPRIORITIZE_ORDERS":
        if "priority_at_least" in p and p["priority_at_least"] not in PRIORITIES:
            errors.append("REPRIORITIZE_ORDERS: unknown priority")
            return None
        if "zones" in p:
            p["zones"] = [z for z in p["zones"] if z in zones]
        p["boost_minutes"] = int(_clamp(p.get("boost_minutes", 3), 0, 15, 3))
    elif t == "SEND_TO_CHARGE":
        ids = [
            r
            for r in p.get("robot_ids", [])
            if r in robots and robots[r].status.operational and robots[r].status != RobotStatus.CHARGING
        ]
        if not ids:
            errors.append("SEND_TO_CHARGE: no valid robots")
            return None
        if len(ids) > max(1, len(robots) // 4):
            ids = ids[: max(1, len(robots) // 4)]
            errors.append("SEND_TO_CHARGE: capped at a quarter of the fleet")
        p["robot_ids"] = ids
        p["after_current_task"] = bool(p.get("after_current_task", True))
    elif t in ("REROUTE_AVOID_ZONE",):
        zs = [z for z in p.get("zones", []) if z in zones]
        if not zs:
            errors.append(f"{t}: no valid zones")
            return None
        p["zones"] = zs
        p["penalty"] = _clamp(p.get("penalty", 4.0), 0.5, 10.0, 4.0)
        p["duration_min"] = int(_clamp(p.get("duration_min", 30), 5, 240, 30))
    elif t == "PREFER_CORRIDOR":
        cs = [c for c in p.get("corridors", []) if c in zones and zones[c].kind.value == "corridor"]
        if not cs:
            errors.append("PREFER_CORRIDOR: no valid corridors")
            return None
        p["corridors"] = cs
        p["bonus"] = _clamp(p.get("bonus", 0.4), 0.05, 0.8, 0.4)
        p["duration_min"] = int(_clamp(p.get("duration_min", 30), 5, 240, 30))
    elif t == "REPOSITION_INVENTORY":
        a, b = p.get("from_zone"), p.get("to_zone")
        if a not in zones or b not in zones or a == b or zones[b].closed:
            errors.append("REPOSITION_INVENTORY: invalid zones")
            return None
        p["skus"] = int(_clamp(p.get("skus", 6), 1, 12, 6))
        p["units"] = int(_clamp(p.get("units", 40), 5, 200, 40))
    elif t == "SET_BATCHING":
        p["orders_per_trip"] = int(_clamp(p.get("orders_per_trip", 2), 1, 4, 2))
    elif t == "SET_ZONE_CAPACITY":
        caps = {z: int(_clamp(c, 1, 30, 3)) for z, c in (p.get("zones") or {}).items() if z in zones}
        if not caps:
            errors.append("SET_ZONE_CAPACITY: no valid zones")
            return None
        p["zones"] = caps
    elif t in ("CLOSE_ZONE", "OPEN_ZONE"):
        z = p.get("zone_id")
        if z not in zones:
            errors.append(f"{t}: unknown zone")
            return None
        if t == "CLOSE_ZONE" and (
            zones[z].kind.value != "storage" or sum(1 for x in world.storage_zones() if not x.closed) <= 1
        ):
            errors.append("CLOSE_ZONE: only storage zones may be closed and at least one must stay open")
            return None
    elif t == "ADD_ROBOTS":
        p["count"] = int(_clamp(p.get("count", 1), 1, 4, 1))
    elif t == "REMOVE_ROBOTS":
        ids = [r for r in p.get("robot_ids", []) if r in robots]
        if not ids or len(ids) > len(robots) // 3:
            errors.append("REMOVE_ROBOTS: invalid ids or more than a third of the fleet")
            return None
        p["robot_ids"] = ids
    elif t == "DISPATCH_WORKER":
        if p.get("worker_id") not in world.workers or p.get("dock_id") not in world.docks:
            errors.append("DISPATCH_WORKER: unknown worker or dock")
            return None
    elif t == "CANCEL_TASKS":
        ids = [x for x in p.get("task_ids", []) if x in world.tasks]
        if not ids:
            errors.append("CANCEL_TASKS: no valid tasks")
            return None
        p["task_ids"] = ids
    elif t == "SET_STRATEGY":
        if p.get("name") not in ("baseline", "optimized", "ai_planner", "nexus_full", "optimized_greedy"):
            errors.append("SET_STRATEGY: unknown strategy")
            return None
    else:
        errors.append(f"unknown action type {t}")
        return None
    return ActionModel(type=t, params=p, rationale=action.rationale)


def validate_plan(world: WorldState, plan: PlanModel) -> PlanModel:
    errors: list[str] = []
    kept: list[ActionModel] = []
    for a in plan.actions:
        v = validate_action(world, a, errors)
        if v is not None:
            kept.append(v)
    if not kept:
        kept = [ActionModel(type="NOOP")]
        errors.append("no valid actions remain")
    plan.actions = kept
    plan.validation_errors = errors
    plan.feasible = not (len(kept) == 1 and kept[0].type == "NOOP" and plan.name.lower().find("nothing") < 0)
    if all(a.type == "NOOP" for a in kept) and "nothing" in plan.name.lower():
        plan.feasible = True
    return plan
