"""Agentic scheduling strategies used in benchmarks and the live twin.

* ``ai_planner`` — the optimized scheduler plus a periodic (and event-triggered) planning cycle in which
  the Planner agent proposes plans and the top-ranked playbook plan is executed **without** simulation
  (an "LLM-assisted" operator).
* ``nexus_full`` — the complete NEXUS loop: candidates are simulated in forked worlds over a short
  horizon, risk-gated, and only the best-scoring plan (if it beats doing nothing) is executed.

Both are deterministic when the LLM is disabled (the default in benchmarks).
"""

from __future__ import annotations

from typing import Any

from nexus.agents.executor import PlanExecutor
from nexus.agents.planner import PlannerAgent
from nexus.agents.risk import RiskAgent
from nexus.agents.simulator import SimulatorAgent, to_outcome
from nexus.agents.situation import analyze
from nexus.agents.validator import validate_plan
from nexus.api.schemas import ActionModel, PlanModel
from nexus.events.types import Event, EventType
from nexus.optimization.strategy import OptimizedStrategy
from nexus.simulation.strategies import register_strategy

TRIGGERS = {
    EventType.ROBOT_FAILURE,
    EventType.ZONE_CLOSED,
    EventType.DOCK_CLOSED,
    EventType.AISLE_BLOCKED,
    EventType.CHARGER_DISABLED,
}


class AiPlannerStrategy(OptimizedStrategy):
    name = "ai_planner"

    def __init__(
        self, decide_every: int = 900, cooldown: int = 300, use_llm: bool = False, **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self.decide_every = decide_every
        self.cooldown = cooldown
        self.use_llm = use_llm
        self._last_decision = -(10**9)
        self._pending = False
        self.decisions_made = 0
        self.plans_executed: list[str] = []
        self.nested = False  # True inside a forked evaluation: never start a decision cycle there

    # ---- hooks ---------------------------------------------------------------------------------
    def on_event(self, engine: Any, event: Event) -> None:
        super().on_event(engine, event)
        if event.type in TRIGGERS and event.origin != "agent":
            self._pending = True

    def tick(self, engine: Any) -> None:
        super().tick(engine)
        if self.nested:
            return
        t = engine.world.clock.tick
        due = t > 0 and t % self.decide_every == 0
        if (due or self._pending) and t - self._last_decision >= self.cooldown:
            self._pending = False
            self._last_decision = t
            self.decision_cycle(engine)

    # ---- planning cycle ------------------------------------------------------------------------
    def _candidates(self, engine: Any) -> list[PlanModel]:
        world = engine.world
        situation = analyze(world, engine, None)
        llm = None
        if self.use_llm:
            from nexus.llm.client import LLMClient

            llm = LLMClient()
        plans, _ = PlannerAgent(llm).propose(
            world, situation, "Minimize SLA breaches", 6, self.use_llm, f"AIP-{world.clock.tick}"
        )
        return [validate_plan(world, p) for p in plans]

    def decision_cycle(self, engine: Any) -> None:
        plans = [
            p for p in self._candidates(engine) if p.feasible and not all(a.type == "NOOP" for a in p.actions)
        ]
        self.decisions_made += 1
        if not plans:
            return
        chosen = plans[0]  # playbook order encodes the planner's own ranking
        PlanExecutor(engine).execute(chosen, origin="agent")
        self.plans_executed.append(chosen.name)

    def describe(self) -> dict:
        d = super().describe()
        d.update(
            {
                "name": self.name,
                "planning": "playbook/LLM planner, top plan executed without simulation",
                "decide_every": self.decide_every,
            }
        )
        return d


class NexusFullStrategy(AiPlannerStrategy):
    name = "nexus_full"

    def __init__(
        self, candidates: int = 4, sim_horizon: int = 1200, risk_gate: str = "HIGH", **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self.candidates = candidates
        self.sim_horizon = sim_horizon
        self.risk_gate = risk_gate
        self.simulated_total = 0

    def decision_cycle(self, engine: Any) -> None:
        world = engine.world
        plans = [p for p in self._candidates(engine) if p.feasible]
        self.decisions_made += 1
        noop = next((p for p in plans if all(a.type == "NOOP" for a in p.actions)), None)
        if noop is None:
            noop = PlanModel(
                id=f"NX-{world.clock.tick}-P0",
                name="Do nothing (reference)",
                source="heuristic",
                description="",
                actions=[ActionModel(type="NOOP")],
            )
        others = [p for p in plans if p is not noop][: self.candidates]
        if not others:
            return
        batch = [noop, *others]
        # never use worker processes inside a strategy (it may itself be running inside one)
        self.nested = True  # the pickled clone evaluating candidates must not recurse into its own cycle
        try:
            results = SimulatorAgent(workers=1, sample_every=300).simulate(
                world, self, batch, self.sim_horizon, engine.faults.remaining()
            )
        finally:
            self.nested = False
        self.simulated_total += len(batch)
        base = results[0]
        best_plan, best_res = None, None
        for plan, res in zip(others, results[1:], strict=True):
            if res["score"] < base["score"] - 1e-9 and (best_res is None or res["score"] < best_res["score"]):
                best_plan, best_res = plan, res
        if best_plan is None or best_res is None:
            return
        best_plan.simulation = to_outcome(best_res, base)
        risk = RiskAgent().assess(
            best_plan, best_plan.simulation, to_outcome(base, base), None, world.summary()
        )
        order = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
        if order.index(risk.level) >= order.index(self.risk_gate):
            return
        PlanExecutor(engine).execute(best_plan, origin="agent")
        self.plans_executed.append(best_plan.name)

    def describe(self) -> dict:
        d = super().describe()
        d.update(
            {
                "name": self.name,
                "planning": f"simulate {self.candidates} candidates over {self.sim_horizon} ticks, risk-gated (< {self.risk_gate}), execute best",
            }
        )
        return d


register_strategy("ai_planner", AiPlannerStrategy)
register_strategy("nexus_full", NexusFullStrategy)
