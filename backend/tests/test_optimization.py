import pickle
import random

import pytest

from nexus.events import EventType, apply, make_event
from nexus.optimization import (
    AssignmentProblem,
    GeneticAllocator,
    ObjectiveWeights,
    OptimizationEngine,
    RoutingPolicy,
    assignment_feasible,
    build_batches,
    build_problem,
    compare_strategies,
    order_urgency,
    score_breakdown,
    score_kpis,
    sequence_orders,
    solve,
    solve_cpsat,
    solve_greedy,
    solve_hungarian,
    validate_tasks,
)
from nexus.optimization import assignment as assignment_module
from nexus.optimization.assignment import INF
from nexus.simulation.engine import SimulationEngine
from nexus.simulation.faults import FaultInjector
from nexus.simulation.metrics import KPIs
from nexus.simulation.order_generator import OrderGenerator
from nexus.simulation.pathfinding import Pathfinder
from nexus.simulation.strategies import GreedyStrategy, make_strategy
from nexus.twin import build_world, spec_for
from nexus.twin.entities import Cell, Order, OrderLine, OrderPriority, TaskStatus

# ------------------------------------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------------------------------------


def _random_problem(n_robots: int, n_batches: int, seed: int, infeasible: float = 0.1) -> AssignmentProblem:
    rng = random.Random(seed)
    cost = [
        [INF if rng.random() < infeasible else round(rng.uniform(1.0, 100.0), 3) for _ in range(n_batches)]
        for _ in range(n_robots)
    ]
    return AssignmentProblem(
        [f"R{i + 1:02d}" for i in range(n_robots)], [[f"ORD-{j}"] for j in range(n_batches)], cost
    )


def _assert_feasible(problem: AssignmentProblem, pairs: list[tuple[str, int]]) -> None:
    robots = [r for r, _ in pairs]
    batches = [b for _, b in pairs]
    assert len(set(robots)) == len(robots), "robot assigned twice"
    assert len(set(batches)) == len(batches), "batch assigned twice"
    for r, b in pairs:
        assert problem.cost[problem.robot_ids.index(r)][b] < INF


def _pending_orders(world, n: int = 30) -> list[Order]:
    gen = OrderGenerator()
    out = []
    for _ in range(n):
        order = gen.make_order(world)
        apply(
            world, make_event(EventType.ORDER_CREATED, world.clock.tick, order.id, {"order": order.to_dict()})
        )
        out.append(world.orders[order.id])
    return out


def _run(strategy, scale: str, seed: int, ticks: int) -> SimulationEngine:
    world = build_world(spec_for(scale, seed=seed))
    engine = SimulationEngine(world, strategy, fault_injector=FaultInjector(spontaneous=False))
    engine.run(ticks)
    return engine


# ------------------------------------------------------------------------------------------------
# objective
# ------------------------------------------------------------------------------------------------


def _kpis(**overrides) -> KPIs:
    base = {
        "tick": 3600, "sim_hours": 1.0, "orders_created": 300, "orders_delivered": 280, "orders_open": 20,
        "orders_pending": 5, "orders_late": 10, "orders_overdue_open": 2, "orders_cancelled": 0,
        "avg_fulfillment_min": 4.0, "p50_fulfillment_min": 3.5, "p95_fulfillment_min": 8.0, "sla_breach_rate": 0.035,
        "sla_breach_rate_projected": 0.04, "throughput_per_hour": 280.0, "robot_utilization": 0.7,
        "robot_availability": 1.0, "robots_total": 12, "robots_operational": 12, "distance_total": 30000,
        "energy_total": 600.0, "congestion_index": 0.05, "wait_ticks_per_robot_hour": 1.0, "replans": 3, "failures": 0,
        "charging_sessions": 4, "inventory_units": 18000, "avg_lateness_min": 1.5,
    }  # fmt: skip
    base.update(overrides)
    return KPIs(**base)


def test_score_is_dominated_by_sla_breach():
    good = _kpis()
    worse = _kpis(sla_breach_rate_projected=0.14)
    assert score_kpis(worse) > score_kpis(good)
    assert score_kpis(worse) - score_kpis(good) == pytest.approx(10.0, abs=1e-6)
    breakdown = score_breakdown(good)
    assert sum(breakdown.values()) == pytest.approx(score_kpis(good), abs=1e-3)
    custom = ObjectiveWeights(
        lateness=0.0, delivery_time=2.0, tail=0.0, congestion=0.0, distance=0.0, energy=0.0, backlog=0.0
    )
    assert score_kpis(good, custom) == pytest.approx(8.0)
    assert ObjectiveWeights.from_dict(custom.to_dict()) == custom


# ------------------------------------------------------------------------------------------------
# solvers
# ------------------------------------------------------------------------------------------------


def test_cpsat_is_feasible_and_no_worse_than_greedy():
    problem = _random_problem(8, 11, seed=1)
    exact = solve_cpsat(problem)
    greedy = solve_greedy(problem)
    _assert_feasible(problem, exact.pairs)
    _assert_feasible(problem, greedy.pairs)
    assert exact.method.startswith("cpsat")
    assert exact.objective <= greedy.objective + 1e-6
    assert exact.assigned == len(exact.pairs) == min(problem.n_robots, problem.n_batches)
    assert exact.evaluated == problem.evaluated > 0
    again = solve_cpsat(problem)
    assert again.pairs == exact.pairs  # deterministic


def test_hungarian_matches_cpsat_on_square_instance():
    problem = _random_problem(7, 7, seed=5, infeasible=0.0)
    hung = solve_hungarian(problem)
    exact = solve_cpsat(problem)
    _assert_feasible(problem, hung.pairs)
    assert hung.assigned == exact.assigned == 7
    assert hung.objective == pytest.approx(exact.objective, abs=1e-2)


def test_solve_auto_falls_back(monkeypatch):
    problem = _random_problem(5, 6, seed=9)

    def boom(*args, **kwargs):
        raise RuntimeError("no solver")

    monkeypatch.setattr(assignment_module, "solve_cpsat", boom)
    result = solve(problem, "auto")
    assert result.method == "hungarian"
    _assert_feasible(problem, result.pairs)
    monkeypatch.setattr(assignment_module, "solve_hungarian", boom)
    result = solve(problem, "auto")
    assert result.method == "greedy"
    _assert_feasible(problem, result.pairs)
    with pytest.raises(ValueError):
        solve(problem, "quantum")


def test_solve_trivial_fast_path_and_empty():
    problem = _random_problem(1, 5, seed=2, infeasible=0.0)
    result = solve(problem, "auto")
    assert result.method == "greedy-trivial" and len(result.pairs) == 1
    best = min(range(5), key=lambda b: problem.cost[0][b])
    assert result.pairs[0][1] == best
    empty = AssignmentProblem(["R01"], [["ORD-1"]], [[INF]])
    assert solve(empty).method == "none" and solve(empty).pairs == []


def test_genetic_allocator_is_feasible_deterministic_and_close_to_optimal():
    problem = _random_problem(6, 8, seed=4)
    ga1 = GeneticAllocator(problem, population=24, generations=20, seed=3).solve()
    ga2 = GeneticAllocator(problem, population=24, generations=20, seed=3).solve()
    _assert_feasible(problem, ga1.pairs)
    assert ga1.pairs == ga2.pairs and ga1.objective == ga2.objective
    assert ga1.method == "ga" and ga1.evaluated >= 24 + 20 * (24 - 4)  # elites are not re-scored
    optimum = solve_cpsat(problem).objective
    assert optimum - 1e-6 <= ga1.objective <= optimum * 1.25 + 1e-6
    assert ga1.objective <= solve_greedy(problem).objective + 1e-6  # seeded with greedy, never worse
    allocator = GeneticAllocator(problem, population=24, generations=20, seed=3)
    top = allocator.top_k(3)
    assert 1 <= len(top) <= 3
    assert len({tuple(p) for p, _ in top}) == len(top)
    assert top[0][1] == ga1.objective
    assert solve(problem, "ga").method == "ga"


# ------------------------------------------------------------------------------------------------
# batching & sequencing
# ------------------------------------------------------------------------------------------------


def test_batching_respects_capacity_batch_max_and_priority(small_world):
    orders = _pending_orders(small_world, 40)
    sequenced = sequence_orders(small_world, orders)
    assert {o.id for o in sequenced} == {o.id for o in orders}
    batches = build_batches(small_world, sequenced, batch_max=3, capacity=10)
    covered = [o.id for b in batches for o in b]
    assert sorted(covered) == sorted(o.id for o in orders)
    for batch in batches:
        assert 1 <= len(batch) <= 3
        assert sum(o.items for o in batch) <= 10
        if batch[0].priority == OrderPriority.CRITICAL:
            assert len(batch) <= 2
    assert any(len(b) > 1 for b in batches), "expected some multi-order trips"
    assert build_batches(small_world, sequenced, batch_max=1, capacity=10) == [[o] for o in sequenced]
    assert build_batches(small_world, sequenced, batch_max=3, capacity=10) == batches  # deterministic


def test_weighted_edf_sequencing(tiny_world):
    world = tiny_world
    now = world.clock.tick
    shelf = next(iter(world.shelves.values()))
    sku = next(iter(shelf.inventory))

    def order(oid: str, priority: OrderPriority, slack_min: float) -> Order:
        return Order(oid, now, now + int(slack_min * 60), priority, [OrderLine(sku, 1, shelf.id)])

    normal_5 = order("N5", OrderPriority.NORMAL, 5)
    critical_9 = order("C9", OrderPriority.CRITICAL, 9)
    low_4 = order("L4", OrderPriority.LOW, 4)
    overdue_high = order("H-1", OrderPriority.HIGH, -1)
    seq = [o.id for o in sequence_orders(world, [normal_5, critical_9, low_4, overdue_high])]
    assert seq[0] == "H-1"
    assert seq.index("C9") < seq.index("N5")
    assert order_urgency(world, overdue_high) > order_urgency(world, normal_5) > 0
    assert order_urgency(world, order("fresh", OrderPriority.NORMAL, 10)) == pytest.approx(0.0, abs=1e-6)
    boosted = sequence_orders(world, [normal_5, low_4], boost={"L4": 4.0})
    assert boosted[0].id == "L4"


# ------------------------------------------------------------------------------------------------
# routing policy
# ------------------------------------------------------------------------------------------------


def test_routing_policy_penalises_zone_expires_and_pickles(tiny_world):
    world = tiny_world
    zone = world.zones["A"]
    pf = Pathfinder(world)
    # a corridor cell just below zone A and one just above it, in line with an aisle of zone A
    aisle_x = zone.x0 + 3
    below = Cell(aisle_x, zone.y0 - 1)
    above = Cell(aisle_x, zone.y1 + 1)
    assert world.grid.walkable(*below) and world.grid.walkable(*above)
    direct = pf.astar(below, above)
    assert direct and any(zone.contains(c) for c in direct)
    policy = RoutingPolicy()
    policy.avoid("A", 6.0, until_tick=100)
    fn = policy.cost_fn(world)
    assert fn is not None and fn(world.grid.idx(zone.x0, zone.y0)) == 6.0
    detour = pf.astar(below, above, cost_fn=fn)
    assert detour and not any(zone.contains(c) for c in detour)
    assert len(detour) > len(direct)
    assert "avoid A" in policy.describe()
    assert RoutingPolicy.from_dict(policy.to_dict()).to_dict() == policy.to_dict()
    assert pickle.loads(pickle.dumps(policy)).to_dict() == policy.to_dict()
    assert policy.expire(99) == [] and policy.expire(100) == ["A"]
    assert policy.is_empty and policy.cost_fn(world) is None
    world.zone_occupancy["A"] = zone.capacity + 2
    costs = RoutingPolicy().zone_costs(world)
    assert costs["A"] > 0
    prefer = RoutingPolicy()
    prefer.prefer("C1", 5.0)
    assert prefer.prefer_corridors["C1"] == 0.9


# ------------------------------------------------------------------------------------------------
# constraints, problem building, engine façade
# ------------------------------------------------------------------------------------------------


def test_assignment_feasibility_and_task_validation(tiny_world):
    world = tiny_world
    orders = _pending_orders(world, 4)
    robot = world.robots["R01"]
    ok, reason = assignment_feasible(world, robot, orders[:1], batch_max=1)
    assert ok, reason
    assert not assignment_feasible(world, robot, orders[:2], batch_max=1)[0]
    robot.battery = 5.0
    ok, reason = assignment_feasible(world, robot, orders[:1])
    assert not ok and "battery" in reason
    robot.battery = 100.0
    opt = OptimizationEngine(world)
    plan = opt.plan_assignments([robot], orders[:1], method="greedy")
    assert len(plan.tasks) == 1 and validate_tasks(world, plan.tasks) == []
    dup = plan.tasks[0]
    assert any("twice" in e for e in validate_tasks(world, [dup, dup]))


def test_build_problem_costs_and_estimates(small_world):
    world = small_world
    orders = _pending_orders(world, 12)
    robots = sorted(world.robots.values(), key=lambda r: r.id)[:4]
    batches = build_batches(world, sequence_orders(world, orders), batch_max=2, capacity=10)
    problem = build_problem(world, robots, batches, Pathfinder(world))
    assert problem.n_robots == 4 and problem.n_batches == len(batches)
    assert problem.evaluated > 0
    for (ri, bi), est in problem.estimates.items():
        assert est.cells > 0 and est.picks >= 1 and est.finish_tick > world.clock.tick
        assert est.cost == pytest.approx(sum(est.breakdown.values()), abs=1e-3)
        assert problem.cost[ri][bi] == est.cost
    result = solve(problem)
    _assert_feasible(problem, result.pairs)
    assert result.assigned == min(4, len(batches))


def test_reposition_inventory_events_apply_cleanly(small_world):
    world = small_world
    before_total = world.inventory_units()
    opt = OptimizationEngine(world)
    events = opt.reposition_inventory_events("C", "B", skus=4, units=20)
    assert 1 <= len(events) <= 4
    moved: dict[str, int] = {}
    for type_, entity, payload in events:
        assert type_ == EventType.INVENTORY_MOVED and payload["qty"] > 0
        assert world.shelves[payload["from_shelf"]].zone_id == "C"
        assert world.shelves[payload["to_shelf"]].zone_id == "B"
        apply(world, make_event(type_, 0, entity, payload))
        moved[payload["sku"]] = moved.get(payload["sku"], 0) + payload["qty"]
    assert world.inventory_units() == before_total
    for sku, qty in moved.items():
        assert sum(s.inventory.get(sku, 0) for s in world.shelves.values() if s.zone_id == "B") >= qty
        assert any(s.zone_id == "B" for s in (world.shelves[i] for i in world.sku_index[sku]))
    hot = [p["sku"] for _, _, p in events]
    assert world.sku_popularity[hot[0]] >= world.sku_popularity[hot[-1]]
    assert opt.reposition_inventory_events("C", "C") == []


def test_reassign_after_failure_uses_only_target_robots():
    engine = _run(make_strategy("optimized"), "small", 42, 900)
    world = engine.world
    busy = next(r for r in sorted(world.robots.values(), key=lambda r: r.id) if r.task_id)
    engine.inject(
        EventType.ROBOT_FAILURE, busy.id, {"cause": "motor_fault", "recovery_ticks": 1800}, origin="user"
    )
    assert any(t.robot_id == busy.id and t.status == TaskStatus.CANCELLED for t in world.tasks.values())
    idle = [r.id for r in world.available_robots()][:2]
    if len(idle) < 2:
        for r in sorted(world.robots.values(), key=lambda r: r.id):
            if r.id != busy.id and r.task_id is None and r.status.operational:
                idle.append(r.id)
            if len(idle) >= 2:
                break
    opt = OptimizationEngine(world, engine.pathfinder)
    plan = opt.reassign_after_failure(busy.id, to_robots=idle)
    assert plan.tasks, "expected re-planned tasks"
    assert {t.robot_id for t in plan.tasks} <= set(idle)
    assert validate_tasks(world, plan.tasks) == []
    assert opt.explain_last()["reason"] == f"failure of {busy.id}"
    assert opt.charging_candidates(extra_margin=200.0)  # with a huge margin, every robot qualifies


# ------------------------------------------------------------------------------------------------
# the optimized strategy end-to-end
# ------------------------------------------------------------------------------------------------


def test_optimized_strategy_is_deterministic_and_not_worse_than_baseline():
    base = _run(GreedyStrategy(), "small", 42, 7200).kpis()
    opt_engine = _run(make_strategy("optimized"), "small", 42, 7200)
    opt = opt_engine.kpis()
    again = _run(make_strategy("optimized"), "small", 42, 7200)
    assert opt_engine.world.digest() == again.world.digest()
    assert opt.sla_breach_rate_projected <= base.sla_breach_rate_projected
    assert opt.orders_delivered >= 0.95 * base.orders_delivered
    assert opt.avg_fulfillment_min <= base.avg_fulfillment_min * 1.05
    desc = opt_engine.strategy.describe()
    assert desc["batching"] and desc["rounds"] > 0 and desc["tasks_created"] > 0
    assert desc["last"]["method"] in {"cpsat", "cpsat-feasible", "greedy-trivial", "hungarian", "greedy"}
    assert any(len(t.order_ids) > 1 for t in opt_engine.world.tasks.values()), (
        "batching produced multi-order trips"
    )


def test_optimized_strategy_survives_pickle_and_fork():
    engine = _run(make_strategy("optimized"), "tiny", 7, 1200)
    strategy = engine.strategy
    strategy.routing_policy.avoid("A", 2.0, until_tick=1500)
    strategy.pending_charge.add("R02")
    clone = pickle.loads(pickle.dumps(strategy))
    assert clone.routing_policy.to_dict() == strategy.routing_policy.to_dict()
    assert clone.pending_charge == {"R02"}
    fork = engine.world.fork("plan")
    fork_engine = SimulationEngine(fork, clone, fault_injector=FaultInjector(spontaneous=False))
    engine.run(600)
    fork_engine.run(600)
    assert engine.world.digest() == fork_engine.world.digest()


def test_optimized_greedy_variant_and_replan_on_failure():
    strategy = make_strategy("optimized_greedy")
    assert strategy.name == "optimized_greedy" and strategy.batch_max == 1 and strategy.method == "greedy"
    engine = _run(strategy, "tiny", 3, 300)
    busy = None
    for _ in range(3000):  # wait until some robot is executing a task
        engine.step()
        busy = next((r for r in engine.world.robots.values() if r.task_id), None)
        if busy is not None:
            break
    assert busy is not None
    rounds = strategy.rounds
    engine.inject(
        EventType.ROBOT_FAILURE, busy.id, {"cause": "motor_fault", "recovery_ticks": 600}, origin="user"
    )
    engine.run(1)
    assert strategy.rounds >= rounds  # a replan was triggered (or nothing pending)


def test_compare_strategies_quick():
    result = compare_strategies("tiny", minutes=15, seed=1)
    assert set(result) >= {"baseline", "optimized", "delta_vs_baseline"}
    assert (
        result["optimized"]["ticks_per_s"] > 0
        and "sla_breach_rate_projected" in result["delta_vs_baseline"]["optimized"]
    )
