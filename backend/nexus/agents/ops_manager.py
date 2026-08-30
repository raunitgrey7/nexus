"""Operations Manager agent — the orchestrator of the decision pipeline.

    Goal → Forecast → Situation → Planner (LLM + playbooks) → Constraint validation → Optimizer
         → Simulation of every candidate in forked worlds → Risk (incl. stability re-runs)
         → Approval policy → (Human) → Executor on the live world

Every decision is a durable :class:`DecisionModel` record with timings, so the pipeline is auditable
and benchmarkable ("planning latency").
"""

from __future__ import annotations

import pickle
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from nexus.agents.executor import PlanExecutor
from nexus.agents.explain import explain_decision
from nexus.agents.planner import PlannerAgent
from nexus.agents.policy import ApprovalPolicy
from nexus.agents.risk import RiskAgent
from nexus.agents.simulator import SimulatorAgent, to_outcome
from nexus.agents.situation import Situation, analyze
from nexus.agents.validator import validate_plan
from nexus.api.schemas import ActionModel, ApprovalModel, DecisionModel, PlanModel
from nexus.core.config import settings
from nexus.core.logging import get_logger
from nexus.events.types import Event, EventType
from nexus.llm.client import LLMClient

if TYPE_CHECKING:
    from nexus.simulation.engine import SimulationEngine

log = get_logger("nexus.agents.ops")
TRIGGER_EVENTS = {
    EventType.ROBOT_FAILURE,
    EventType.ZONE_CLOSED,
    EventType.DOCK_CLOSED,
    EventType.AISLE_BLOCKED,
    EventType.CHARGER_DISABLED,
    EventType.DEMAND_CHANGED,
}


class OptimizerAgent:
    """Refines plans with the optimization engine (fills in concrete robot choices, marks optimized)."""

    def refine(self, world: Any, plan: PlanModel) -> PlanModel:
        for a in plan.actions:
            if a.type == "REASSIGN_TASKS" and not a.params.get("to_robots"):
                failed_zone = None
                for rid in a.params.get("from_robots", []):
                    if rid in world.robots:
                        failed_zone = world.robots[rid].zone_id
                        break
                center = (
                    world.zones[failed_zone].center if failed_zone and failed_zone in world.zones else None
                )
                robots = [
                    r
                    for r in world.robots.values()
                    if r.status.operational and r.battery > world.config.battery_low_threshold
                ]
                robots.sort(key=lambda r: (r.cell.manhattan(center) if center else 0, -r.battery, r.id))
                a.params["to_robots"] = [r.id for r in robots[:3]]
            if a.type in (
                "REASSIGN_TASKS",
                "SET_BATCHING",
                "SET_STRATEGY",
                "REPOSITION_INVENTORY",
                "REROUTE_AVOID_ZONE",
                "PREFER_CORRIDOR",
            ):
                plan.optimized = True
        return plan


class OperationsManager:
    def __init__(
        self,
        engine: SimulationEngine,
        llm: LLMClient | None = None,
        forecaster: Any | None = None,
        history: Any | None = None,
        workers: int | None = None,
        candidate_plans: int | None = None,
        horizon_ticks: int | None = None,
        risk_seeds: int | None = None,
        policy: ApprovalPolicy | None = None,
        lock: threading.RLock | None = None,
    ) -> None:
        self.engine = engine
        self.lock = lock or threading.RLock()
        self.llm = llm
        self.forecaster = forecaster
        self.recorder = history
        self.planner = PlannerAgent(llm)
        self.optimizer = OptimizerAgent()
        self.simulator = SimulatorAgent(workers if workers is not None else settings.decision_workers)
        self.risk = RiskAgent()
        self.policy = policy or ApprovalPolicy(settings.auto_approve_max_risk, settings.auto_approve_min_gain)
        self.candidate_plans = candidate_plans or settings.candidate_plans
        self.horizon_ticks = horizon_ticks or settings.sim_horizon_ticks
        self.risk_seeds = settings.risk_seeds if risk_seeds is None else risk_seeds
        self.decisions: dict[str, DecisionModel] = {}
        self.order: list[str] = []
        self.situations: dict[str, Situation] = {}
        self.autopilot = False
        self.cooldown_ticks = 900
        self._last_decision_tick = -(10**9)
        self._pending_trigger: str | None = None
        self._lock = threading.Lock()
        self.listeners: list[Callable[[DecisionModel], None]] = []
        self.busy = False

    # ---- triggers (autopilot) ------------------------------------------------------------------
    def on_event(self, event: Event) -> None:
        if event.type in TRIGGER_EVENTS and event.origin != "agent":
            self._pending_trigger = f"{event.type.value}:{event.entity_id or ''}"

    def poll_trigger(self) -> str | None:
        """Returns a pending autopilot trigger (and clears it) if the cooldown has elapsed."""
        if not self.autopilot or self._pending_trigger is None or self.busy:
            return None
        if self.engine.world.clock.tick - self._last_decision_tick < self.cooldown_ticks:
            return None
        trigger, self._pending_trigger = self._pending_trigger, None
        return trigger

    # ---- the pipeline --------------------------------------------------------------------------
    def decide(
        self,
        goal: str = "Minimize SLA breaches and fulfillment delay",
        trigger: str = "manual",
        horizon_ticks: int | None = None,
        n_candidates: int | None = None,
        use_llm: bool | None = None,
        context: dict[str, Any] | None = None,
        world_snapshot: Any | None = None,
    ) -> DecisionModel:
        self.busy = True
        try:
            return self._decide(
                goal,
                trigger,
                horizon_ticks or self.horizon_ticks,
                n_candidates or self.candidate_plans,
                use_llm,
                context or {},
                world_snapshot,
            )
        finally:
            self.busy = False

    def _decide(
        self,
        goal: str,
        trigger: str,
        horizon: int,
        n_candidates: int,
        use_llm: bool | None,
        context: dict[str, Any],
        world_snapshot: Any | None,
    ) -> DecisionModel:
        t_start = time.perf_counter()
        timings: dict[str, float] = {}
        engine = self.engine
        with self.lock:
            world = world_snapshot if world_snapshot is not None else engine.world.fork("decision")
            strategy = pickle.loads(pickle.dumps(engine.strategy))
            if hasattr(strategy, "nested"):
                strategy.nested = (
                    True  # candidate plans are evaluated without the live controller re-deciding inside
                )
            faults = engine.faults.remaining()
            decision_id = f"DEC-{engine.world.clock.tick:06d}-{len(self.order) + 1:03d}"
            self._last_decision_tick = engine.world.clock.tick

        # 1. forecast + situation
        t0 = time.perf_counter()
        forecast = None
        if self.forecaster is not None:
            try:
                forecast = self.forecaster.forecast(world, self.recorder, engine.pathfinder)
            except Exception as exc:
                log.warning("ops.forecast_failed", error=str(exc)[:200])
        situation = analyze(world, engine, forecast)
        timings["forecast_ms"] = round((time.perf_counter() - t0) * 1000, 1)

        # 2. plan
        t0 = time.perf_counter()
        plans, meta = self.planner.propose(world, situation, goal, n_candidates, use_llm, decision_id)
        timings["planning_ms"] = round((time.perf_counter() - t0) * 1000, 1)

        # 3. validate + optimize
        t0 = time.perf_counter()
        plans = [self.optimizer.refine(world, validate_plan(world, p)) for p in plans]
        if not any(all(a.type == "NOOP" for a in p.actions) for p in plans):
            plans.insert(
                0,
                PlanModel(
                    id=f"{decision_id}-P0",
                    name="Do nothing (reference)",
                    source="heuristic",
                    description="Baseline.",
                    actions=[ActionModel(type="NOOP")],
                ),
            )
        timings["optimization_ms"] = round((time.perf_counter() - t0) * 1000, 1)

        # 4. simulate every candidate
        t0 = time.perf_counter()
        results = self.simulator.simulate(world, strategy, plans, horizon, faults)
        baseline_idx = next(i for i, p in enumerate(plans) if all(a.type == "NOOP" for a in p.actions))
        baseline_res = results[baseline_idx]
        baseline = to_outcome(baseline_res, baseline_res)
        for plan, res in zip(plans, results, strict=True):
            plan.simulation = to_outcome(res, baseline_res)
        timings["simulation_ms"] = round((time.perf_counter() - t0) * 1000, 1)

        # 5. rank (feasible plans by score; ties → fewer actions)
        ranked = sorted(
            (p for p in plans if p.feasible and p.simulation is not None),
            key=lambda p: (p.simulation.score if p.simulation else 0.0, len(p.actions), p.id),
        )
        for i, p in enumerate(ranked):
            p.rank = i + 1
        recommended = ranked[0] if ranked else plans[baseline_idx]
        if (
            recommended.simulation is not None
            and baseline.score <= recommended.simulation.score
            and not all(a.type == "NOOP" for a in recommended.actions)
        ):
            recommended = plans[baseline_idx]

        # 6. risk (with stability re-runs of the recommended plan)
        t0 = time.perf_counter()
        stability_outcomes = []
        if self.risk_seeds > 0 and not all(a.type == "NOOP" for a in recommended.actions):
            salts = [17 * (i + 1) for i in range(self.risk_seeds)]
            extra = self.simulator.simulate(
                world, strategy, [recommended] * len(salts), horizon, faults, seed_salts=salts
            )
            stability_outcomes = [to_outcome(r, baseline_res) for r in extra]
        for p in ranked[:3]:
            if p.simulation is None:
                continue
            p.risk = self.risk.assess(
                p, p.simulation, baseline, stability_outcomes if p is recommended else None, world.summary()
            )
        if recommended.risk is None and recommended.simulation is not None:
            recommended.risk = self.risk.assess(
                recommended, recommended.simulation, baseline, stability_outcomes, world.summary()
            )
        timings["risk_ms"] = round((time.perf_counter() - t0) * 1000, 1)

        # 7. approval
        approval: ApprovalModel = self.policy.decide(recommended, baseline, engine.world.clock.tick)
        timings["total_ms"] = round((time.perf_counter() - t_start) * 1000, 1)
        timings["llm_ms"] = meta.get("llm_latency_ms", 0.0)
        evaluated = int(sum(max(1, r["diagnostics"].get("replans", 0) * 0 + 1) for r in results)) + len(
            stability_outcomes
        )
        allocations = sum(int(r["diagnostics"].get("allocations", 0)) for r in results) + sum(
            int(r.get("events_applied", 0)) for r in results
        )
        decision = DecisionModel(
            id=decision_id,
            created_tick=engine.world.clock.tick,
            sim_time=engine.world.clock.now().isoformat(),
            trigger=trigger,
            goal=goal,
            status="approved" if approval.auto_approved else "proposed",
            situation=situation.to_dict() | {"context": context, "horizon_ticks": horizon},
            baseline=baseline,
            candidates=plans,
            recommended_plan_id=recommended.id,
            approval=approval,
            explanation="",
            timings=timings,
            candidates_evaluated=max(evaluated, len(plans)),
            llm_used=bool(meta.get("llm_used")),
            llm_model=meta.get("llm_model"),
        )
        decision.situation["allocations_considered"] = allocations
        decision.explanation = explain_decision(
            decision,
            situation,
            horizon * engine.world.clock.tick_seconds // 60,
            self.llm if use_llm is not False else None,
        )
        with self._lock:
            self.decisions[decision.id] = decision
            self.order.append(decision.id)
            self.situations[decision.id] = situation
        with self.lock:
            engine.inject(
                EventType.PLAN_PROPOSED,
                None,
                {
                    "plan_id": recommended.id,
                    "decision_id": decision.id,
                    "candidates": len(plans),
                    "recommended": recommended.name,
                },
                origin="agent",
                key=f"{decision.id}:proposed",
            )
            if approval.auto_approved:
                engine.inject(
                    EventType.PLAN_APPROVED,
                    None,
                    {"plan_id": recommended.id, "decision_id": decision.id, "by": "policy"},
                    origin="agent",
                    key=f"{decision.id}:approved",
                )
        self._notify(decision)
        log.info(
            "ops.decision",
            id=decision.id,
            trigger=trigger,
            candidates=len(plans),
            recommended=recommended.name,
            status=decision.status,
            total_ms=timings["total_ms"],
        )
        return decision

    # ---- lifecycle -----------------------------------------------------------------------------
    def get(self, decision_id: str) -> DecisionModel | None:
        return self.decisions.get(decision_id)

    def history(self, limit: int = 20) -> list[DecisionModel]:
        return [self.decisions[i] for i in reversed(self.order[-limit:])]

    def approve(self, decision_id: str, actor: str = "operator", plan_id: str | None = None) -> DecisionModel:
        d = self._require(decision_id)
        if plan_id and any(p.id == plan_id for p in d.candidates):
            d.recommended_plan_id = plan_id
        d.approval = ApprovalModel(
            policy="human",
            auto_approved=False,
            reason=f"Approved by {actor}.",
            approved_by=actor,
            approved_tick=self.engine.world.clock.tick,
        )
        d.status = "approved"
        with self.lock:
            self.engine.inject(
                EventType.PLAN_APPROVED,
                None,
                {"plan_id": d.recommended_plan_id, "decision_id": d.id, "by": actor},
                origin="user",
                key=f"{d.id}:approved:{actor}",
            )
        self._notify(d)
        return d

    def reject(self, decision_id: str, actor: str = "operator", note: str = "") -> DecisionModel:
        d = self._require(decision_id)
        d.status = "rejected"
        d.approval = ApprovalModel(
            policy="human",
            auto_approved=False,
            reason=f"Rejected by {actor}: {note or 'no reason given'}",
            approved_by=actor,
        )
        with self.lock:
            self.engine.inject(
                EventType.PLAN_REJECTED,
                None,
                {"plan_id": d.recommended_plan_id, "decision_id": d.id, "by": actor, "note": note},
                origin="user",
                key=f"{d.id}:rejected",
            )
        self._notify(d)
        return d

    def execute(self, decision_id: str, actor: str = "operator") -> DecisionModel:
        d = self._require(decision_id)
        if d.status not in ("approved",):
            raise ValueError(f"decision {decision_id} is {d.status}; approve it first")
        plan = next((p for p in d.candidates if p.id == d.recommended_plan_id), None)
        if plan is None:
            raise ValueError("recommended plan not found")
        with self.lock:
            events = PlanExecutor(self.engine).execute(plan, origin="agent")
            d.status = "executed"
            d.situation["executed_tick"] = self.engine.world.clock.tick
            d.situation["executed_events"] = len(events)
            self.engine.inject(
                EventType.PLAN_EXECUTED,
                None,
                {"plan_id": plan.id, "decision_id": d.id, "events": len(events), "by": actor},
                origin="agent",
                key=f"{d.id}:executed",
            )
        self._notify(d)
        log.info("ops.executed", id=d.id, plan=plan.name, events=len(events))
        return d

    def decide_and_maybe_execute(self, trigger: str) -> DecisionModel:
        d = self.decide(trigger=trigger)
        if d.status == "approved":
            self.execute(d.id, actor="autopilot")
        return d

    # ---- helpers -------------------------------------------------------------------------------
    def _require(self, decision_id: str) -> DecisionModel:
        d = self.decisions.get(decision_id)
        if d is None:
            raise KeyError(decision_id)
        return d

    def _notify(self, decision: DecisionModel) -> None:
        for fn in list(self.listeners):
            try:
                fn(decision)
            except Exception as exc:
                log.warning("ops.listener_failed", error=str(exc)[:120])

    def stats(self) -> dict[str, Any]:
        return {
            "decisions": len(self.order),
            "autopilot": self.autopilot,
            "busy": self.busy,
            "llm": self.llm.status() if self.llm else None,
            "strategy_pickle_bytes": len(pickle.dumps(self.engine.strategy)),
        }
