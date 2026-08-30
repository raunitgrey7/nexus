"""Intent routing for the natural-language console.

Deterministic keyword/regex classification first (fast, testable, works offline); the LLM only
refines *unknown* questions. Parameter extraction turns "what if demand doubles and R07 fails" into a
scenario the what-if engine can run.
"""

from __future__ import annotations

import re
from typing import Any

from nexus.api.schemas import MutationModel, ScenarioModel
from nexus.twin.world import WorldState

WORD_NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "a": 1,
    "an": 1,
    "half": 0.5,
}
RE_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent)")
RE_ROBOT = re.compile(r"\bR(\d{1,3})\b", re.I)
RE_ORDER = re.compile(r"\bORD-?(\d+)\b", re.I)
RE_ZONE = re.compile(r"\bzone\s+([A-Z]{1,2})\b", re.I)
RE_DOCK = re.compile(r"\b(?:dock|loading dock)\s*(?:D)?(\d)\b", re.I)
RE_CHARGER = re.compile(r"\bCH(\d{2})\b", re.I)
RE_WORKER = re.compile(r"\bW(\d{2})\b")
RE_MINUTES = re.compile(r"(\d+)\s*(?:min|minutes)")
RE_COUNT_ROBOTS = re.compile(
    r"\b(\d+|one|two|three|four|five|six|a|an)\s+(?:more\s+|extra\s+|additional\s+|fewer\s+)?robots?\b", re.I
)
RE_TASK = re.compile(r"\bTASK-?(\d+)\b", re.I)


def _num(token: str) -> float:
    token = token.lower()
    if token in WORD_NUMBERS:
        return WORD_NUMBERS[token]
    return float(token)


def extract_params(question: str) -> dict[str, Any]:
    q = question.strip()
    ql = q.lower()
    params: dict[str, Any] = {}
    if m := RE_PERCENT.search(ql):
        params["percent"] = float(m.group(1))
    robots = [f"R{int(r):02d}" for r in RE_ROBOT.findall(q)]
    if robots:
        params["robot_ids"] = robots
    if m := RE_ORDER.search(q):
        params["order_id"] = f"ORD-{int(m.group(1)):06d}"
    if m := RE_ZONE.search(q):
        params["zone"] = m.group(1).upper()
    if m := RE_DOCK.search(q):
        params["dock"] = f"D{m.group(1)}"
    if m := RE_CHARGER.search(q):
        params["charger"] = f"CH{m.group(1)}"
    if m := RE_WORKER.search(q):
        params["worker"] = f"W{m.group(1)}"
    if m := RE_TASK.search(q):
        params["task_id"] = f"TASK-{int(m.group(1)):06d}"
    if m := RE_MINUTES.search(ql):
        params["minutes"] = int(m.group(1))
    if m := RE_COUNT_ROBOTS.search(ql):
        params["robots"] = int(_num(m.group(1)))
    if "double" in ql or "twice" in ql or "2x" in ql:
        params["multiplier"] = 2.0
    elif "triple" in ql:
        params["multiplier"] = 3.0
    elif "half" in ql or "halve" in ql:
        params["multiplier"] = 0.5
    elif "percent" in params or "%" in ql:
        pct = params.get("percent", 0.0)
        sign = -1.0 if re.search(r"\b(drop|decrease|fall|reduce|less|lower)", ql) else 1.0
        params["multiplier"] = 1.0 + sign * pct / 100.0
    if re.search(r"\b(fail|fails|failure|breaks|broken|down|offline)\b", ql):
        params["failure"] = True
    if re.search(r"\b(remove|withdraw|lose|without|take out|fewer)\b", ql):
        params["remove"] = True
    if re.search(r"\b(add|extra|more|additional)\b", ql):
        params["add"] = True
    if re.search(r"\b(close|closed|closes|inaccessible|blocked|unavailable|shut)\b", ql):
        params["closure"] = True
    if re.search(r"\b(charg)", ql):
        params["charging"] = True
    if re.search(r"\b(demand|order volume|orders|volume|traffic)\b", ql):
        params["demand"] = True
    if re.search(r"\b(batch|batching)\b", ql):
        params["batching"] = True
    if re.search(r"\b(worker|loader|staff)\b", ql) and re.search(r"\b(late|delay|absent|sick)\b", ql):
        params["worker_delay"] = True
    if re.search(r"\baisle\b", ql):
        params["aisle"] = True
    if re.search(r"\b(inventory|stock|sku)s?\b", ql) and re.search(
        r"\b(move|reposition|relocate|shift)\b", ql
    ):
        params["move_inventory"] = True
        zones = [z.upper() for z in RE_ZONE.findall(q)]
        if len(zones) >= 2:
            params["from_zone"], params["to_zone"] = zones[0], zones[1]
    return params


def classify(question: str) -> tuple[str, dict[str, Any]]:
    ql = question.lower().strip()
    params = extract_params(question)
    has_id = any(k in params for k in ("robot_ids", "order_id", "task_id", "charger", "worker"))
    if re.search(
        r"\b(what if|what would happen|what happens if|suppose|assume|scenario|if we|if demand|if .* fails?)\b",
        ql,
    ) or (ql.startswith("if ")):
        return "whatif", params
    if re.search(r"^why\b|\bwhy\b|what is causing|what's causing|cause of|root cause|reason for", ql):
        return "explain", params
    if re.search(
        r"\b(should|recommend|recommendation|what (can|should|do) (we|i)|best (plan|option|action)|how (do|can) we (fix|improve|reduce|recover)|fix this|mitigate)\b",
        ql,
    ):
        return "recommend", params
    if re.search(
        r"\b(forecast|predict|prediction|next (hour|\d+ ?min|30|60|90)|will (we|demand|the)|expect|going to|bottleneck|upcoming|later today)\b",
        ql,
    ):
        return "forecast", params
    if (
        has_id
        or re.search(
            r"\b(where is|status of|show me|tell me about|details? (of|on|for)|which robot|which zone)\b", ql
        )
    ) and ("zone" in params or has_id or re.search(r"\b(where is|status of|tell me about|details)\b", ql)):
        return "entity", params
    if re.search(
        r"\b(how many|status|current|right now|kpi|utili[sz]ation|throughput|breach|open orders|backlog|overview|summary|how (are|is) (we|the|things))\b",
        ql,
    ):
        return "status", params
    if re.search(r"\b(slow|delay|late|behind)\b", ql):
        return "explain", params
    return "unknown", params


def build_scenario(params: dict[str, Any], world: WorldState) -> ScenarioModel:
    mutations: list[MutationModel] = []
    names: list[str] = []
    robots = [r for r in params.get("robot_ids", []) if r in world.robots]
    if params.get("failure") and robots:
        mutations.append(
            MutationModel(
                type="ROBOT_FAILURE",
                params={
                    "robot_ids": robots,
                    "cause": "motor_fault",
                    "recovery_min": params.get("minutes", 45),
                },
            )
        )
        names.append(f"{', '.join(robots)} fail")
    elif params.get("failure") and params.get("robots"):
        ids = sorted(world.robots)[: int(params["robots"])]
        mutations.append(
            MutationModel(
                type="ROBOT_FAILURE",
                params={"robot_ids": ids, "cause": "motor_fault", "recovery_min": params.get("minutes", 45)},
            )
        )
        names.append(f"{len(ids)} robots fail")
    if params.get("remove") and (params.get("robots") or robots):
        p = {"robot_ids": robots} if robots else {"count": int(params["robots"])}
        mutations.append(MutationModel(type="REMOVE_ROBOTS", params=p))
        names.append(f"remove {len(robots) or params['robots']} robots")
    if params.get("add") and params.get("robots"):
        mutations.append(MutationModel(type="ADD_ROBOTS", params={"count": int(params["robots"])}))
        names.append(f"add {int(params['robots'])} robots")
    if params.get("demand") and "multiplier" in params and not params.get("charging"):
        mult = float(params["multiplier"])
        if params.get("minutes"):
            mutations.append(
                MutationModel(
                    type="DEMAND_BURST", params={"multiplier": mult, "duration_min": params["minutes"]}
                )
            )
            names.append(f"demand ×{mult:.2f} for {params['minutes']} min")
        else:
            mutations.append(MutationModel(type="DEMAND_MULTIPLIER", params={"multiplier": mult}))
            names.append(f"demand ×{mult:.2f}")
    if (
        params.get("closure")
        and params.get("zone")
        and params["zone"] in world.zones
        and not params.get("aisle")
    ):
        mutations.append(
            MutationModel(
                type="CLOSE_ZONE", params={"zone_id": params["zone"], "reopen_min": params.get("minutes", 60)}
            )
        )
        names.append(f"zone {params['zone']} closed")
    if params.get("closure") and params.get("dock") and params["dock"] in world.docks:
        mutations.append(MutationModel(type="CLOSE_DOCK", params={"dock_id": params["dock"]}))
        names.append(f"dock {params['dock']} closed")
    if params.get("aisle") and params.get("zone"):
        mutations.append(
            MutationModel(
                type="BLOCK_AISLE",
                params={"zone_id": params["zone"], "aisles": 1, "clear_min": params.get("minutes", 30)},
            )
        )
        names.append(f"aisle blocked in zone {params['zone']}")
    if params.get("charging"):
        count = int(params.get("robots", 0)) or max(
            1,
            round(len(world.chargers) * (1 - float(params.get("multiplier", 0.5))))
            if "multiplier" in params
            else max(1, len(world.chargers) // 2),
        )
        mutations.append(MutationModel(type="DISABLE_CHARGERS", params={"count": count}))
        names.append(f"{count} chargers disabled")
    if params.get("worker_delay"):
        mutations.append(
            MutationModel(
                type="WORKER_DELAY",
                params={
                    "worker_ids": [params["worker"]] if params.get("worker") else None,
                    "minutes": params.get("minutes", 30),
                },
            )
        )
        names.append("worker delay")
    if params.get("batching"):
        mutations.append(MutationModel(type="SET_BATCHING", params={"orders_per_trip": 3}))
        names.append("batching 3/trip")
    if params.get("move_inventory") and params.get("from_zone") and params.get("to_zone"):
        mutations.append(
            MutationModel(
                type="MOVE_INVENTORY",
                params={
                    "from_zone": params["from_zone"],
                    "to_zone": params["to_zone"],
                    "skus": 6,
                    "units": 40,
                },
            )
        )
        names.append(f"move inventory {params['from_zone']}→{params['to_zone']}")
    if not mutations:  # a hypothetical we could not parse: default to the signature incident
        mutations.append(
            MutationModel(
                type="ROBOT_FAILURE",
                params={
                    "robot_ids": ["R07" if "R07" in world.robots else sorted(world.robots)[0]],
                    "cause": "motor_fault",
                    "recovery_min": 45,
                },
            )
        )
        names.append("R07 fails (default scenario)")
    return ScenarioModel(
        name=" + ".join(names), description="from natural-language question", mutations=mutations
    )


SUGGESTIONS = [
    "Why are orders slowing down?",
    "What happens if order volume increases by 40%?",
    "What if robot R07 fails right now?",
    "What if we remove two robots?",
    "Which robot should charge next?",
    "What should we do about zone C congestion?",
    "Forecast the next 60 minutes",
    "Where is R03 and what is it doing?",
]
