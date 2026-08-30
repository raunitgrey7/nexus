import pytest

from nexus.events import EventType, verify_replay
from nexus.simulation import FaultInjector, ScheduledFault, SimulationEngine, compute_kpis
from nexus.simulation.pathfinding import Pathfinder
from nexus.simulation.strategies import GreedyStrategy
from nexus.twin import build_world, spec_for
from nexus.twin.entities import Cell, CellType, OrderStatus, RobotStatus
from tests.conftest import make_engine


def test_engine_is_deterministic():
    a = make_engine("tiny", 11)
    b = make_engine("tiny", 11)
    a.run(900)
    b.run(900)
    assert a.world.digest() == b.world.digest()
    assert a.store.stats()["counts"] == b.store.stats()["counts"]
    c = make_engine("tiny", 12)
    c.run(900)
    assert c.world.digest() != a.world.digest()


def test_orders_flow_end_to_end():
    eng = make_engine("tiny", 5)
    eng.run(2400)
    k = eng.kpis()
    assert k.orders_created > 20
    assert k.orders_delivered > 0.6 * k.orders_created
    assert k.orders_delivered == eng.world.stats.orders_delivered
    delivered = [o for o in eng.world.orders.values() if o.status == OrderStatus.DELIVERED]
    assert all(o.delivered_tick is not None and o.delivered_tick >= o.created_tick for o in delivered)
    assert all(all(line.picked for line in o.lines) for o in delivered)
    assert k.robot_utilization > 0.05
    assert eng.world.stats.distance_total > 0
    counts = eng.store.stats()["counts"]
    assert counts["ITEM_PICKED"] >= k.orders_delivered and counts["ORDER_DELIVERED"] == k.orders_delivered


def test_fork_continues_identically():
    eng = make_engine("tiny", 3)
    eng.run(600)
    fork = eng.world.fork("f")
    fork_engine = SimulationEngine(fork, GreedyStrategy(), fault_injector=FaultInjector(spontaneous=False))
    eng.run(600)
    fork_engine.run(600)
    assert eng.world.digest() == fork_engine.world.digest()


def test_replay_with_external_events():
    faults = [
        ScheduledFault(300, EventType.ROBOT_FAILURE, "R02", {"cause": "motor_fault", "recovery_ticks": 600}),
        ScheduledFault(500, EventType.ZONE_CLOSED, "B", {"reason": "spill"}),
        ScheduledFault(900, EventType.ZONE_OPENED, "B", {}),
    ]
    eng = make_engine("tiny", 21, faults=faults)
    snapshot = eng.world.snapshot_bytes()
    eng.run(1500)
    assert eng.world.stats.failures_total == 1
    assert len(eng.store.external_events()) == 3
    assert verify_replay(
        eng,
        snapshot,
        lambda w: SimulationEngine(w, GreedyStrategy(), fault_injector=FaultInjector(spontaneous=False)),
    )


def test_robot_failure_releases_orders_and_recovers():
    faults = [
        ScheduledFault(200, EventType.ROBOT_FAILURE, "R01", {"cause": "lidar_fault", "recovery_ticks": 300})
    ]
    eng = make_engine("tiny", 8, faults=faults)
    eng.run(201)
    r = eng.world.robots["R01"]
    assert r.status == RobotStatus.FAILED and r.task_id is None
    assert all(o.robot_id != "R01" for o in eng.world.open_orders())
    eng.run(400)
    assert r.status.operational
    eng.run(600)
    assert r.orders_completed > 0


def test_low_battery_triggers_charging():
    eng = make_engine("tiny", 4)
    for robot in eng.world.robots.values():
        robot.battery = 21.0
    eng.run(1500)
    counts = eng.store.stats()["counts"]
    assert counts.get("BATTERY_LOW", 0) >= 1 and counts.get("CHARGING_STARTED", 0) >= 1
    assert eng.world.stats.charging_sessions >= 1
    assert any(r.battery > 30 for r in eng.world.robots.values())


def test_zone_closure_keeps_robots_out():
    faults = [ScheduledFault(50, EventType.ZONE_CLOSED, "A", {"reason": "maintenance"})]
    eng = make_engine("tiny", 9, faults=faults)
    eng.run(60)
    zone = eng.world.zones["A"]
    for _ in range(900):
        eng.step()
        for r in eng.world.robots.values():
            assert not zone.contains(r.cell) or eng.world.clock.tick < 55, "robot inside a closed zone"
    assert eng.world.stats.orders_delivered > 0  # other zones keep flowing


def test_aisle_block_forces_replan():
    eng = make_engine("tiny", 13)
    world = eng.world
    r = None
    for _ in range(3000):  # wait until some robot is en route with a few cells ahead of it
        eng.step()
        moving = [x for x in world.robots.values() if len(x.path) > 4]
        if moving:
            r = moving[0]
            break
    assert r is not None, "expected robots en route"
    target = r.path[2]
    eng.inject(EventType.AISLE_BLOCKED, None, {"cells": [list(target)]}, origin="user", key="blk")
    assert (
        eng.inject(EventType.AISLE_BLOCKED, None, {"cells": [list(target)]}, origin="user", key="blk") is None
    )
    eng.run(30)
    assert target not in r.path
    assert r.cell != target


def test_pathfinder_astar_and_bfs(tiny_world):
    pf = Pathfinder(tiny_world)
    start = tiny_world.robots["R01"].cell
    shelf = next(iter(tiny_world.shelves.values()))
    path = pf.astar(start, shelf.access_cell)
    assert path and path[-1] == shelf.access_cell
    prev = start
    for c in path:
        assert prev.manhattan(c) == 1 and tiny_world.grid.walkable(*c)
        prev = c
    assert pf.distance(start, shelf.access_cell) == len(path)
    assert pf.astar(start, shelf.cell) is None  # shelves are not walkable
    # cache hit on repeat
    hits = pf.cache_hits
    pf.astar(start, shelf.access_cell)
    assert pf.cache_hits == hits + 1
    # avoid set changes the path
    detour = pf.astar(start, shelf.access_cell, avoid=[path[0]])
    assert detour is None or detour[0] != path[0]
    # congestion cost steers away from penalised cells
    penalised = {c.y * tiny_world.grid.width + c.x for c in path[: len(path) // 2]}
    alt = pf.astar(start, shelf.access_cell, cost_fn=lambda i: 5.0 if i in penalised else 0.0)
    assert alt is not None and sum(1 for c in alt if c.y * tiny_world.grid.width + c.x in penalised) <= len(
        penalised
    )


def test_order_generator_rate():
    world = build_world(spec_for("tiny", seed=2, orders_per_hour=360))
    world.demand.hourly_multipliers = [1.0] * 24
    eng = SimulationEngine(world, GreedyStrategy(), fault_injector=FaultInjector(spontaneous=False))
    eng.run(3600)
    assert 300 <= eng.world.stats.orders_created <= 420
    for order in eng.world.orders.values():
        assert 1 <= len(order.lines) <= world.demand.max_lines
        assert order.deadline_tick > order.created_tick


def test_kpis_window_and_projection():
    eng = make_engine("tiny", 6)
    eng.run(1200)
    full = compute_kpis(eng.world)
    window = compute_kpis(eng.world, since_tick=600)
    assert window.orders_delivered <= full.orders_delivered
    assert 0 <= full.sla_breach_rate_projected <= 1
    assert full.throughput_per_hour > 0
    d = full.to_dict()
    assert set(d) >= {"sla_breach_rate_projected", "avg_fulfillment_min", "robot_utilization"}


def test_spontaneous_failures_are_seeded():
    world = build_world(spec_for("tiny", seed=5, robot_failure_rate_per_hour=2.0))
    eng = SimulationEngine(world, GreedyStrategy(), fault_injector=FaultInjector(spontaneous=True))
    eng.run(3600)
    assert eng.world.stats.failures_total >= 1
    world2 = build_world(spec_for("tiny", seed=5, robot_failure_rate_per_hour=2.0))
    eng2 = SimulationEngine(world2, GreedyStrategy(), fault_injector=FaultInjector(spontaneous=True))
    eng2.run(3600)
    assert eng.world.digest() == eng2.world.digest()


@pytest.mark.slow
def test_small_scale_three_hours_runs_near_capacity():
    eng = make_engine("small", 42)
    eng.run(10800)
    k = eng.kpis()
    assert k.orders_created > 800
    assert 0.5 < k.robot_utilization < 0.98
    assert k.orders_delivered > 0.6 * k.orders_created  # the naive baseline runs near/over capacity by 11:00


def test_cell_types_round_trip(tiny_world):
    rows = tiny_world.grid.rows()
    assert rows[0].count(str(int(CellType.DOCK))) == len(tiny_world.docks)
    assert Cell(1, 2).manhattan(Cell(4, 6)) == 7
