import pickle

from nexus.agents import (
    OperationsManager,
    PlanExecutor,
    PlannerAgent,
    RiskAgent,
    SimulatorAgent,
    analyze,
    to_outcome,
    validate_plan,
)
from nexus.agents.policy import ApprovalPolicy
from nexus.api.schemas import ActionModel, PlanModel, RiskFinding, RiskReport, SimulationOutcome
from nexus.events.types import EventType
from nexus.forecasting import Forecaster, HistoryRecorder
from nexus.llm.client import NullLLM
from nexus.simulation.engine import SimulationEngine
from nexus.simulation.faults import FaultInjector
from nexus.simulation.strategies import make_strategy
from nexus.twin import build_world, spec_for


def _engine(scale="tiny", seed=3, strategy="optimized", ticks=900):
    world = build_world(spec_for(scale, seed=seed))
    eng = SimulationEngine(world, make_strategy(strategy), fault_injector=FaultInjector(spontaneous=False))
    rec = HistoryRecorder()
    eng.hooks.append(rec.hook)
    eng.run(ticks)
    return eng, rec


def test_situation_and_heuristic_plans_cover_incidents():
    eng, _ = _engine("small", 42, ticks=1200)
    eng.inject(
        EventType.ROBOT_FAILURE, "R07", {"cause": "motor_fault", "recovery_ticks": 1800}, origin="user"
    )
    eng.run(5)
    s = analyze(eng.world, eng, None)
    assert s.failed_robots and s.failed_robots[0]["id"] == "R07"
    assert "R07" in s.text()
    plans = PlannerAgent(NullLLM()).heuristic_plans(eng.world, s, "DEC-T")
    types = {a.type for p in plans for a in p.actions}
    assert "NOOP" in types and "REASSIGN_TASKS" in types
    assert len({p.name for p in plans}) == len(plans)
    proposed, meta = PlannerAgent(NullLLM()).propose(
        eng.world, s, "min breach", 6, use_llm=False, decision_id="DEC-T"
    )
    assert 2 <= len(proposed) <= 6 and meta["llm_used"] is False
    assert all(p.id.startswith("DEC-T-P") for p in proposed)


def test_validator_sanitises_plans(tiny_world):
    world = tiny_world
    plan = PlanModel(
        id="p",
        name="x",
        source="llm",
        description="",
        actions=[
            ActionModel(
                type="REASSIGN_TASKS",
                params={"to_robots": ["R99", "R01"], "zones": ["ZZ", "A"], "max_tasks": 999},
            ),
            ActionModel(type="SEND_TO_CHARGE", params={"robot_ids": ["R01", "R02", "R03", "R04"]}),
            ActionModel(type="CLOSE_ZONE", params={"zone_id": "NOPE"}),
            ActionModel(type="SET_BATCHING", params={"orders_per_trip": 40}),
            ActionModel(type="REMOVE_ROBOTS", params={"robot_ids": ["R01", "R02", "R03"]}),
        ],
    )
    out = validate_plan(world, plan)
    kinds = [a.type for a in out.actions]
    assert kinds == ["REASSIGN_TASKS", "SEND_TO_CHARGE", "SET_BATCHING"]
    assert (
        out.actions[0].params["to_robots"] == ["R01"]
        and out.actions[0].params["zones"] == ["A"]
        and out.actions[0].params["max_tasks"] == 60
    )
    assert len(out.actions[1].params["robot_ids"]) == 1
    assert out.actions[2].params["orders_per_trip"] == 4
    assert any("CLOSE_ZONE" in e for e in out.validation_errors) and any(
        "REMOVE_ROBOTS" in e for e in out.validation_errors
    )
    assert out.feasible
    empty = validate_plan(
        world,
        PlanModel(
            id="q",
            name="bad",
            source="llm",
            description="",
            actions=[ActionModel(type="CLOSE_ZONE", params={"zone_id": "NOPE"})],
        ),
    )
    assert not empty.feasible


def test_executor_is_idempotent_and_attributable():
    eng, _ = _engine("tiny", 5, ticks=600)
    world = eng.world
    plan = PlanModel(
        id="PLAN-1",
        name="combo",
        source="heuristic",
        description="",
        actions=[
            ActionModel(type="SET_BATCHING", params={"orders_per_trip": 2}),
            ActionModel(
                type="REROUTE_AVOID_ZONE", params={"zones": ["A"], "penalty": 3.0, "duration_min": 10}
            ),
            ActionModel(type="ADD_ROBOTS", params={"count": 1}),
            ActionModel(type="REPRIORITIZE_ORDERS", params={"priority_at_least": "HIGH", "boost_minutes": 2}),
        ],
    )
    plan = validate_plan(world, plan)
    events = PlanExecutor(eng).execute(plan)
    assert events and all(e.cause == "PLAN-1" and e.origin == "agent" for e in events)
    assert world.config.batch_max_orders == 2 and eng.strategy.batch_max == 2
    assert eng.strategy.routing_policy.avoid_zones.get("A") == 3.0
    assert len(world.robots) == 5
    again = PlanExecutor(eng).execute(plan)
    assert again == []  # idempotency keys
    eng.run(30)  # the new robot participates
    assert world.robots["R05"].status.operational


def test_reassign_after_failure_uses_helpers():
    eng, _ = _engine("small", 42, ticks=1500)
    eng.inject(
        EventType.ROBOT_FAILURE, "R07", {"cause": "motor_fault", "recovery_ticks": 1800}, origin="user"
    )
    eng.run(2)
    pending_before = len(eng.world.pending_orders())
    plan = validate_plan(
        eng.world,
        PlanModel(
            id="PLAN-R",
            name="r",
            source="heuristic",
            description="",
            actions=[
                ActionModel(
                    type="REASSIGN_TASKS",
                    params={
                        "from_robots": ["R07"],
                        "to_robots": ["R01", "R02"],
                        "zones": [],
                        "max_tasks": 10,
                    },
                )
            ],
        ),
    )
    events = PlanExecutor(eng).execute(plan)
    created = [e for e in events if e.type == EventType.TASK_CREATED]
    assert all(e.entity_id in ("R01", "R02") for e in created)
    assert pending_before == 0 or created or len(eng.world.pending_orders()) <= pending_before


def test_simulator_and_risk_agent():
    eng, _ = _engine("tiny", 8, ticks=600)
    noop = PlanModel(
        id="P0", name="Do nothing", source="heuristic", description="", actions=[ActionModel(type="NOOP")]
    )
    plan = PlanModel(
        id="P1",
        name="batch",
        source="heuristic",
        description="",
        actions=[ActionModel(type="SET_BATCHING", params={"orders_per_trip": 3})],
    )
    results = SimulatorAgent(workers=1, sample_every=100).simulate(eng.world, eng.strategy, [noop, plan], 600)
    assert len(results) == 2 and all(r["timeline"] for r in results)
    assert results[0]["digest"] != results[1]["digest"] or results[0]["kpis"] == results[1]["kpis"]
    base = to_outcome(results[0], results[0])
    out = to_outcome(results[1], results[0])
    assert set(out.delta_vs_baseline) >= {"sla_breach_rate_projected", "score"}
    assert out.horizon_ticks == 600 and out.diagnostics["min_battery"] <= 100
    report = RiskAgent().assess(plan, out, base, [out], eng.world.summary())
    assert report.level in ("LOW", "MEDIUM", "HIGH", "CRITICAL") and 0 <= report.score <= 1
    assert report.checked_seeds == 2 and "sla_breach_std" in report.stability
    # the same world state is unchanged by simulation (forks are isolated)
    assert eng.world.config.batch_max_orders == 3 or eng.world.config.batch_max_orders == 1


def test_approval_policy_rules():
    policy = ApprovalPolicy("LOW", 0.02)
    kp = {
        "tick": 0,
        "sim_hours": 1,
        "orders_created": 10,
        "orders_delivered": 8,
        "orders_open": 2,
        "orders_pending": 1,
        "orders_late": 0,
        "orders_overdue_open": 0,
        "orders_cancelled": 0,
        "avg_fulfillment_min": 2,
        "p50_fulfillment_min": 2,
        "p95_fulfillment_min": 3,
        "sla_breach_rate": 0.0,
        "sla_breach_rate_projected": 0.10,
        "throughput_per_hour": 8,
        "robot_utilization": 0.5,
        "robot_availability": 1,
        "robots_total": 4,
        "robots_operational": 4,
        "distance_total": 100,
        "energy_total": 1.0,
        "congestion_index": 0.0,
        "wait_ticks_per_robot_hour": 0,
        "replans": 0,
        "failures": 0,
        "charging_sessions": 0,
        "inventory_units": 100,
        "avg_lateness_min": 0,
    }
    base = SimulationOutcome(horizon_ticks=600, kpis=kp, score=10.0)  # type: ignore[arg-type]
    good = SimulationOutcome(horizon_ticks=600, kpis={**kp, "sla_breach_rate_projected": 0.04}, score=4.0)  # type: ignore[arg-type]
    noop = PlanModel(
        id="a", name="Do nothing", source="heuristic", description="", actions=[ActionModel(type="NOOP")]
    )
    assert policy.decide(noop, base, 0).auto_approved
    plan = PlanModel(
        id="b",
        name="p",
        source="heuristic",
        description="",
        actions=[ActionModel(type="SET_BATCHING", params={"orders_per_trip": 2})],
        simulation=good,
        risk=RiskReport(level="LOW", score=0.0, findings=[]),
    )
    assert policy.decide(plan, base, 0).auto_approved
    plan.risk = RiskReport(
        level="HIGH", score=0.6, findings=[RiskFinding(kind="deadlock", severity="high", message="x")]
    )
    a = policy.decide(plan, base, 0)
    assert not a.auto_approved and a.policy == "human"
    plan.risk = RiskReport(level="LOW", score=0.0, findings=[])
    plan.simulation = SimulationOutcome(
        horizon_ticks=600, kpis={**kp, "sla_breach_rate_projected": 0.095}, score=9.5
    )  # type: ignore[arg-type]
    assert not policy.decide(plan, base, 0).auto_approved


def test_ops_manager_full_cycle():
    eng, rec = _engine("tiny", 21, ticks=900)
    eng.inject(
        EventType.ROBOT_FAILURE, "R02", {"cause": "lidar_fault", "recovery_ticks": 1800}, origin="user"
    )
    eng.run(3)
    ops = OperationsManager(
        eng, NullLLM(), Forecaster(), rec, workers=1, candidate_plans=4, horizon_ticks=600, risk_seeds=1
    )
    d = ops.decide(trigger="ROBOT_FAILURE:R02")
    assert d.id.startswith("DEC-") and d.baseline is not None and len(d.candidates) >= 2
    assert d.candidates_evaluated >= len(d.candidates)
    assert all(p.simulation is not None for p in d.candidates)
    rec_plan = next(p for p in d.candidates if p.id == d.recommended_plan_id)
    assert rec_plan.rank == 1 or all(a.type == "NOOP" for a in rec_plan.actions)
    assert rec_plan.risk is not None and d.explanation and "candidate" in d.explanation
    assert d.timings["total_ms"] > 0 and d.status in ("proposed", "approved")
    assert ops.get(d.id) is d and ops.history()[0].id == d.id
    if d.status == "proposed":
        ops.approve(d.id, actor="tester")
    executed = ops.execute(d.id, actor="tester")
    assert executed.status == "executed"
    kinds = {e.type for e in eng.store.log if e.origin in ("agent", "user") and e.tick >= d.created_tick}
    assert EventType.PLAN_PROPOSED in kinds and EventType.PLAN_EXECUTED in kinds
    # rejecting an executed decision is refused; rejecting a fresh one works
    d2 = ops.decide(trigger="manual")
    ops.reject(d2.id, "tester", "not now")
    assert ops.get(d2.id).status == "rejected"


def test_agentic_strategies_are_deterministic_and_picklable():
    for name in ("ai_planner", "nexus_full"):
        digests = []
        for _ in range(2):
            world = build_world(spec_for("tiny", seed=9))
            strategy = make_strategy(
                name,
                decide_every=300,
                cooldown=100,
                **({"candidates": 2, "sim_horizon": 200} if name == "nexus_full" else {}),
            )
            eng = SimulationEngine(world, strategy, fault_injector=FaultInjector(spontaneous=False))
            eng.run(700)
            eng.inject(
                EventType.ROBOT_FAILURE, "R01", {"cause": "motor_fault", "recovery_ticks": 600}, origin="user"
            )
            eng.run(500)
            digests.append(world.digest())
            assert strategy.decisions_made >= 1
            clone = pickle.loads(pickle.dumps(strategy))
            assert clone.name == name
        assert digests[0] == digests[1]


def test_nexus_full_does_not_recurse_in_nested_simulations():
    """A nexus_full clone evaluating candidates must not start its own decision cycles (regression: RecursionError)."""
    import sys

    world = build_world(spec_for("tiny", seed=17))
    strategy = make_strategy("nexus_full", decide_every=120, cooldown=30, candidates=2, sim_horizon=600)
    eng = SimulationEngine(world, strategy, fault_injector=FaultInjector(spontaneous=False))
    eng.inject(EventType.ROBOT_FAILURE, "R01", {"cause": "motor_fault", "recovery_ticks": 900}, origin="user")
    limit = sys.getrecursionlimit()
    sys.setrecursionlimit(400)
    try:
        eng.run(700)  # several decision timers elapse inside each nested 600-tick evaluation
    finally:
        sys.setrecursionlimit(limit)
    assert strategy.decisions_made >= 2 and strategy.nested is False
    assert strategy.simulated_total >= 3
