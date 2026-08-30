"""Planner agent: proposes diverse candidate plans.

Two generators feed the candidate pool:

* **Heuristic playbooks** (always on): deterministic templates keyed on the situation (robot failure,
  congestion, demand pressure, battery risk, infrastructure closures). They guarantee coverage and a
  reproducible benchmark.
* **LLM proposals** (when Ollama is reachable): the model sees the situation, forecast and retrieved
  SOPs and returns a structured `PlanSet`. Its plans are validated like any other candidate.

Every plan is *only a proposal*: the optimizer refines it, the simulator scores it, the risk agent judges it.
"""

from __future__ import annotations

import time
from typing import Any, cast

from pydantic import BaseModel, Field

from nexus.agents.situation import Situation
from nexus.api.schemas import ActionModel, PlanModel
from nexus.core.logging import get_logger
from nexus.llm.client import LLMClient
from nexus.llm.prompts import PLANNER_SYSTEM, planner_user_prompt
from nexus.llm.rag import SOPRetriever
from nexus.twin.entities import RobotStatus
from nexus.twin.world import WorldState

log = get_logger("nexus.agents.planner")


class LLMAction(BaseModel):
    type: str
    params: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""


class LLMPlan(BaseModel):
    name: str
    description: str = ""
    actions: list[LLMAction]


class PlanSet(BaseModel):
    plans: list[LLMPlan]


def _plan(
    pid: str, name: str, description: str, actions: list[ActionModel], source: str = "heuristic"
) -> PlanModel:
    return PlanModel(id=pid, name=name, description=description, actions=actions, source=source)  # type: ignore[arg-type]


def _nearest_operational(world: WorldState, zone_id: str | None, k: int, exclude: set[str]) -> list[str]:
    """Operational robots closest (by zone hops, then distance) to ``zone_id``."""
    adjacency = world.zone_adjacency()
    hops: dict[str, int] = {}
    if zone_id and zone_id in world.zones:
        frontier = [zone_id]
        hops[zone_id] = 0
        while frontier:
            nxt = []
            for z in frontier:
                for n in adjacency.get(z, ()):
                    if n not in hops:
                        hops[n] = hops[z] + 1
                        nxt.append(n)
            frontier = nxt
    center = world.zones[zone_id].center if zone_id and zone_id in world.zones else None
    candidates = [
        r
        for r in world.robots.values()
        if r.status.operational and r.id not in exclude and r.status != RobotStatus.CHARGING
    ]
    candidates.sort(
        key=lambda r: (hops.get(r.zone_id, 99), r.cell.manhattan(center) if center else 0, -r.battery, r.id)
    )
    return [r.id for r in candidates[:k]]


def _spare_neighbour(world: WorldState, zone_id: str, load: dict[str, int]) -> str | None:
    """A storage zone near ``zone_id`` (2 hops via corridor) with the least open demand."""
    adjacency = world.zone_adjacency()
    near: set[str] = set()
    for corridor in adjacency.get(zone_id, ()):
        for z in adjacency.get(corridor, ()):
            if z != zone_id and world.zones[z].kind.value == "storage" and not world.zones[z].closed:
                near.add(z)
    if not near:
        return None
    return min(near, key=lambda z: (load.get(z, 0), world.zone_occupancy.get(z, 0), z))


def _corridor_between(world: WorldState, zone_id: str, avoid: str | None = None) -> str | None:
    adjacency = world.zone_adjacency()
    corridors = [
        c
        for c in adjacency.get(zone_id, ())
        if world.zones[c].kind.value == "corridor" and c != avoid and not world.zones[c].closed
    ]
    if not corridors:
        return None
    return min(corridors, key=lambda c: (world.zone_occupancy.get(c, 0), c))


class PlannerAgent:
    def __init__(self, llm: LLMClient | None = None, retriever: SOPRetriever | None = None) -> None:
        self.llm = llm
        self.retriever = retriever or SOPRetriever()

    # ---- public --------------------------------------------------------------------------------
    def propose(
        self,
        world: WorldState,
        situation: Situation,
        goal: str,
        n_candidates: int = 8,
        use_llm: bool | None = None,
        decision_id: str = "DEC",
    ) -> tuple[list[PlanModel], dict[str, Any]]:
        t0 = time.perf_counter()
        plans = self.heuristic_plans(world, situation, decision_id)
        meta: dict[str, Any] = {
            "heuristic_plans": len(plans),
            "llm_plans": 0,
            "llm_used": False,
            "llm_model": None,
            "llm_latency_ms": 0.0,
        }
        want_llm = self.llm is not None and (use_llm if use_llm is not None else True)
        if want_llm and self.llm is not None and self.llm.available():
            t1 = time.perf_counter()
            llm_plans = self.llm_plans(
                world,
                situation,
                goal,
                max(3, min(6, n_candidates - len(plans) + 2)),
                decision_id,
                offset=len(plans),
            )
            meta["llm_latency_ms"] = round((time.perf_counter() - t1) * 1000, 1)
            meta["llm_used"] = bool(llm_plans)
            meta["llm_model"] = self.llm.model if llm_plans else None
            meta["llm_plans"] = len(llm_plans)
            plans = self._dedupe(plans + llm_plans)
        plans = plans[: max(2, n_candidates)]
        for i, p in enumerate(plans):
            p.id = f"{decision_id}-P{i + 1}"
        meta["total_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return plans, meta

    # ---- heuristic playbooks -------------------------------------------------------------------
    def heuristic_plans(self, world: WorldState, s: Situation, decision_id: str) -> list[PlanModel]:
        plans: list[PlanModel] = [
            _plan(
                f"{decision_id}-P0",
                "Do nothing (reference)",
                "Keep the current strategy; used as the comparison baseline.",
                [ActionModel(type="NOOP")],
            )
        ]
        load = s.open_orders_by_zone
        hot = s.hot_zones[0] if s.hot_zones else None
        congested = s.congested_zones[0]["zone"] if s.congested_zones else None
        focus_zone = congested or hot
        failed_ids = {r["id"] for r in s.failed_robots}

        if s.failed_robots:
            zone = s.failed_robots[0]["zone"]
            helpers = _nearest_operational(world, zone, 2, failed_ids)
            helpers_more = _nearest_operational(world, zone, 4, failed_ids)
            zones = [
                z
                for z in [zone, focus_zone]
                if z and z in world.zones and world.zones[z].kind.value == "storage"
            ]
            if helpers:
                plans.append(
                    _plan(
                        f"{decision_id}-Pa",
                        f"Reassign released work to {' & '.join(helpers)}",
                        f"Robots nearest to zone {zone} absorb the tasks released by {', '.join(failed_ids)}.",
                        [
                            ActionModel(
                                type="REASSIGN_TASKS",
                                params={
                                    "from_robots": sorted(failed_ids),
                                    "to_robots": helpers,
                                    "zones": zones,
                                    "max_tasks": 14,
                                },
                                rationale="Adjacent robots minimise extra travel.",
                            ),
                        ],
                    )
                )
                corridor = _corridor_between(world, zone)
                actions = [
                    ActionModel(
                        type="REASSIGN_TASKS",
                        params={
                            "from_robots": sorted(failed_ids),
                            "to_robots": helpers,
                            "zones": zones,
                            "max_tasks": 14,
                        },
                        rationale="Adjacent robots absorb released tasks.",
                    ),
                    ActionModel(
                        type="REPRIORITIZE_ORDERS",
                        params={"priority_at_least": "HIGH", "boost_minutes": 3},
                        rationale="Protect the tightest SLAs while capacity is reduced.",
                    ),
                ]
                if corridor:
                    actions.append(
                        ActionModel(
                            type="PREFER_CORRIDOR",
                            params={"corridors": [corridor], "bonus": 0.4, "duration_min": 30},
                            rationale=f"Spread traffic through {corridor}.",
                        )
                    )
                plans.append(
                    _plan(
                        f"{decision_id}-Pb",
                        f"Reassign to {' & '.join(helpers)}, prioritise HIGH, route via {corridor or 'corridors'}",
                        "Reallocation plus deadline protection and traffic spreading.",
                        actions,
                    )
                )
            if helpers_more:
                plans.append(
                    _plan(
                        f"{decision_id}-Pc",
                        f"Spread released work over {len(helpers_more)} robots + batching",
                        "Wider reallocation with 2 orders per trip to recover throughput.",
                        [
                            ActionModel(
                                type="REASSIGN_TASKS",
                                params={
                                    "from_robots": sorted(failed_ids),
                                    "to_robots": helpers_more,
                                    "zones": zones,
                                    "max_tasks": 20,
                                },
                                rationale="More robots share the load.",
                            ),
                            ActionModel(
                                type="SET_BATCHING",
                                params={"orders_per_trip": min(4, max(2, s.batch_max + 1))},
                                rationale="Batching raises per-robot throughput.",
                            ),
                        ],
                    )
                )

        if congested:
            corridor = _corridor_between(world, congested)
            neighbour = _spare_neighbour(world, congested, load)
            plans.append(
                _plan(
                    f"{decision_id}-Pd",
                    f"Reroute traffic away from zone {congested}",
                    f"Penalise routing through {congested} for 30 minutes and prefer {corridor or 'the nearest corridor'}.",
                    [
                        ActionModel(
                            type="REROUTE_AVOID_ZONE",
                            params={"zones": [congested], "penalty": 4.0, "duration_min": 30},
                            rationale="Reduce robot density in the congested zone.",
                        ),
                        *(
                            [
                                ActionModel(
                                    type="PREFER_CORRIDOR",
                                    params={"corridors": [corridor], "bonus": 0.4, "duration_min": 30},
                                    rationale="Use spare corridor capacity.",
                                )
                            ]
                            if corridor
                            else []
                        ),
                    ],
                )
            )
            if neighbour:
                plans.append(
                    _plan(
                        f"{decision_id}-Pe",
                        f"Pre-position hot inventory from {congested} to {neighbour}",
                        "Move the fastest-moving SKUs out of the congested zone so fewer trips need to enter it.",
                        [
                            ActionModel(
                                type="REPOSITION_INVENTORY",
                                params={"from_zone": congested, "to_zone": neighbour, "skus": 6, "units": 40},
                                rationale="Demand follows inventory; spreading hot SKUs spreads traffic.",
                            ),
                            ActionModel(
                                type="SET_ZONE_CAPACITY",
                                params={"zones": {congested: max(1, world.zones[congested].capacity - 1)}},
                                rationale="Tighter soft capacity makes routing more cautious.",
                            ),
                        ],
                    )
                )

        util = s.kpis.robot_utilization
        pressure = (
            s.backlog > max(6, len(world.robots)) or util > 0.85 or s.kpis.sla_breach_rate_projected > 0.08
        )
        if pressure:
            plans.append(
                _plan(
                    f"{decision_id}-Pf",
                    f"Enable batching ({min(4, max(3, s.batch_max + 1))} orders/trip) + deadline sequencing",
                    "Raise per-robot throughput without adding hardware.",
                    [
                        ActionModel(
                            type="SET_BATCHING",
                            params={"orders_per_trip": min(4, max(3, s.batch_max + 1))},
                            rationale="Batching amortises travel across orders.",
                        ),
                        ActionModel(
                            type="REPRIORITIZE_ORDERS",
                            params={"priority_at_least": "HIGH", "boost_minutes": 2},
                            rationale="Serve the tightest deadlines first.",
                        ),
                    ],
                )
            )
            plans.append(
                _plan(
                    f"{decision_id}-Pg",
                    "Add 2 robots + batching",
                    "Capacity expansion for sustained demand above capacity.",
                    [
                        ActionModel(type="ADD_ROBOTS", params={"count": 2}, rationale="Fleet capacity."),
                        ActionModel(
                            type="SET_BATCHING",
                            params={"orders_per_trip": min(4, max(2, s.batch_max))},
                            rationale="Batching.",
                        ),
                    ],
                )
            )
        if s.low_battery:
            ids = [r["id"] for r in s.low_battery[:3]]
            plans.append(
                _plan(
                    f"{decision_id}-Ph",
                    f"Pre-emptive charging for {', '.join(ids)}",
                    "Charge before exhaustion forces an unplanned stop.",
                    [
                        ActionModel(
                            type="SEND_TO_CHARGE",
                            params={"robot_ids": ids, "after_current_task": True},
                            rationale="Avoid mid-task battery depletion.",
                        ),
                    ],
                )
            )
        if s.closed_docks:
            loaders = [w.id for w in world.workers.values() if w.role == "loader"]
            open_docks = [d.id for d in world.docks.values() if d.open]
            if loaders and open_docks:
                plans.append(
                    _plan(
                        f"{decision_id}-Pi",
                        f"Dispatch loader {loaders[0]} to dock {open_docks[0]}",
                        "Keep unloading fast on the remaining docks.",
                        [
                            ActionModel(
                                type="DISPATCH_WORKER",
                                params={"worker_id": loaders[0], "dock_id": open_docks[0]},
                                rationale="Unloading without a loader takes twice as long.",
                            ),
                        ],
                    )
                )
        if s.strategy == "baseline":
            plans.append(
                _plan(
                    f"{decision_id}-Pj",
                    "Switch to the optimized scheduler",
                    "CP-SAT assignment, batching and congestion-aware routing.",
                    [
                        ActionModel(
                            type="SET_STRATEGY",
                            params={"name": "optimized"},
                            rationale="Optimization beats greedy dispatch under load.",
                        ),
                    ],
                )
            )
        if focus_zone and not congested and s.open_orders_by_zone.get(focus_zone, 0) >= 6:
            corridor = _corridor_between(world, focus_zone)
            if corridor:
                plans.append(
                    _plan(
                        f"{decision_id}-Pk",
                        f"Prefer corridor {corridor} near hot zone {focus_zone}",
                        "Pre-empt congestion where demand is concentrating.",
                        [
                            ActionModel(
                                type="PREFER_CORRIDOR",
                                params={"corridors": [corridor], "bonus": 0.3, "duration_min": 20},
                                rationale="Spread traffic before congestion builds.",
                            ),
                        ],
                    )
                )
        return self._dedupe(plans)

    # ---- LLM -----------------------------------------------------------------------------------
    def llm_plans(
        self, world: WorldState, s: Situation, goal: str, n: int, decision_id: str, offset: int = 0
    ) -> list[PlanModel]:
        if self.llm is None:
            return []
        query = " ".join(
            filter(
                None,
                [
                    "robot failure" if s.failed_robots else "",
                    "congestion zone routing inventory" if s.congested_zones else "",
                    "demand capacity batching" if s.kpis.robot_utilization > 0.8 else "",
                    "battery charging" if s.low_battery else "",
                    "dock closure worker" if s.closed_docks else "",
                    "blocked aisle" if s.blocked_cells else "",
                    "safety simulate",
                ],
            )
        )
        sops = self.retriever.snippets(query, k=3)
        messages = [
            {"role": "system", "content": PLANNER_SYSTEM},
            {
                "role": "user",
                "content": planner_user_prompt(
                    s.text(short=False), s.forecast_summary or "(no forecast)", goal, n, sops
                ),
            },
        ]
        result = self.llm.structured(messages, PlanSet, retries=1)
        if result is None:
            return []
        plans: list[PlanModel] = []
        allowed = set(ActionModel.model_fields["type"].annotation.__args__)  # type: ignore[union-attr]
        for i, lp in enumerate(result.plans):
            actions = [
                ActionModel(type=cast(Any, a.type), params=a.params, rationale=a.rationale)
                for a in lp.actions
                if a.type in allowed
            ]
            if not actions:
                continue
            plans.append(
                _plan(
                    f"{decision_id}-L{offset + i + 1}",
                    lp.name[:80],
                    lp.description[:300],
                    actions,
                    source="llm",
                )
            )
        log.info("planner.llm_plans", count=len(plans), model=self.llm.model)
        return plans

    # ---- helpers -------------------------------------------------------------------------------
    @staticmethod
    def _signature(plan: PlanModel) -> str:
        return "|".join(sorted(f"{a.type}:{sorted(a.params.items())!r}" for a in plan.actions))

    def _dedupe(self, plans: list[PlanModel]) -> list[PlanModel]:
        seen: set[str] = set()
        out: list[PlanModel] = []
        for p in plans:
            sig = self._signature(p)
            if sig in seen:
                continue
            seen.add(sig)
            out.append(p)
        return out
