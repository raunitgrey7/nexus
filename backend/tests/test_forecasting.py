import math
import pickle
import time
from itertools import pairwise

from nexus.events import EventType, apply, make_event
from nexus.forecasting import (
    Forecaster,
    HistoryRecorder,
    detect_bottlenecks,
    forecast_battery,
    forecast_congestion,
    forecast_demand,
    holt_winters,
    linear_trend,
    ses,
)
from nexus.forecasting.smoothing import holt_winters_fit, linear_slope, mape, prediction_interval
from nexus.simulation import FaultInjector, SimulationEngine
from nexus.simulation.strategies import GreedyStrategy
from nexus.twin import build_world, spec_for
from nexus.twin.entities import Order, OrderLine, OrderPriority, RobotStatus
from tests.conftest import make_engine


def _synthetic(n: int, m: int = 12) -> list[float]:
    return [50 + 0.5 * t + 10 * math.sin(2 * math.pi * t / m) + ((t * 7) % 5 - 2) * 0.3 for t in range(n)]


def test_holt_winters_recovers_seasonal_trend():
    m = 12
    series = _synthetic(5 * m, m)
    fc = holt_winters(series[: 4 * m], m, horizon=m)
    assert len(fc) == m
    assert mape(series[4 * m :], fc) < 0.15
    _, fitted, method = holt_winters_fit(series[: 4 * m], m, horizon=1)
    assert method == "holt-winters" and len(fitted) == 4 * m


def test_smoothing_fallbacks_and_helpers():
    assert holt_winters([], 12, horizon=3) == [0.0, 0.0, 0.0]
    assert holt_winters([5.0], 12, horizon=2) == [5.0, 5.0]
    short = holt_winters([1.0, 2.0, 3.0, 4.0, 5.0], 12, horizon=2)
    assert len(short) == 2 and short[0] > 4.0
    assert holt_winters_fit([1.0, 2.0, 3.0], 12, horizon=1)[2] == "ses"
    assert all(math.isfinite(v) for v in linear_trend([3.0, 2.0, 1.0, 0.5], horizon=5, phi=0.8))
    assert ses([2.0, 4.0], alpha=0.5, horizon=1) == [3.0]
    assert abs(linear_slope([0.0, 2.0, 4.0]) - 2.0) < 1e-9
    assert prediction_interval([]) == 0.0 and prediction_interval([1.0, -1.0], z=2.0) == 2.0


def test_history_recorder_cadence_bounds_and_serialisation():
    eng = make_engine("tiny", 3)
    rec = HistoryRecorder(sample_every_ticks=30, max_samples=10)
    eng.hooks.append(rec.hook)
    eng.run(600)
    assert len(rec) == 10  # bounded ring buffer
    ticks = rec.ticks()
    assert all(b - a == 30 for a, b in pairwise(ticks))
    points = rec.timeline_points()
    assert [p.tick for p in points] == ticks
    assert rec.latest() is not None and rec.latest().tick == ticks[-1]
    assert rec.series("orders_created")[-1] == eng.world.stats.orders_created
    assert rec.arrival_rate_per_min(5) >= 0.0
    restored = HistoryRecorder.from_dict(rec.to_dict())
    assert restored.series("open") == rec.series("open") and restored.ticks() == ticks
    fork = rec.fork()
    fork.record(eng.world)
    assert len(fork) == 10 and fork.latest() is not rec.latest()
    assert pickle.loads(pickle.dumps(rec)).ticks() == ticks
    assert rec.robot_battery_series("R01") and rec.zone_series("CHG")


def test_demand_forecast_tracks_engine():
    world = build_world(spec_for("tiny", seed=2, orders_per_hour=180))
    world.demand.hourly_multipliers = [1.0] * 24
    eng = SimulationEngine(world, GreedyStrategy(), fault_injector=FaultInjector(spontaneous=False))
    rec = HistoryRecorder(sample_every_ticks=60)
    eng.hooks.append(rec.hook)
    eng.run(3600)
    horizon = 60
    fc = forecast_demand(world, rec, horizon_min=horizon, bucket_min=15)
    assert (
        fc.horizon_min == horizon and fc.per_bucket[0].start_min == 0 and fc.per_bucket[-1].end_min == horizon
    )
    assert all(b.lower <= b.expected_orders <= b.upper for b in fc.per_bucket)
    assert abs(sum(b.expected_orders for b in fc.per_bucket) - fc.expected_orders) < 0.5
    assert fc.method.endswith("profile-prior") and "holt" in fc.method
    assert 0.2 <= fc.confidence <= 0.95 and fc.capacity_per_hour > 0
    before = world.stats.orders_created
    eng.run(horizon * 60)
    actual = world.stats.orders_created - before
    assert abs(fc.expected_orders - actual) <= 0.5 * actual
    no_history = forecast_demand(world, None, horizon_min=30)
    assert no_history.method == "profile-prior" and no_history.expected_orders > 0


def test_battery_forecast_risk_levels():
    eng = make_engine("tiny", 4)
    eng.run(300)
    world = eng.world
    with_task = next(r for r in world.robots.values() if r.task_id)
    with_task.battery = 15.0
    other = next(r for r in world.robots.values() if r.id != with_task.id)
    other.status = RobotStatus.CHARGING
    other.charger_id = next(iter(world.chargers))
    forecasts = forecast_battery(world, pathfinder=eng.pathfinder)
    assert forecasts[0].robot_id == with_task.id
    low = next(f for f in forecasts if f.robot_id == with_task.id)
    assert low.risk == "high" and low.predicted_exhaustion_min is not None and low.charger_eta_min is not None
    assert "charg" in low.recommendation.lower() and with_task.id in low.recommendation
    charging = next(f for f in forecasts if f.robot_id == other.id)
    assert charging.predicted_exhaustion_min is None and charging.risk == "low"
    assert all(f.workload_tasks >= 0 for f in forecasts)


def _inject_orders_for_zone(world, zone_id: str, count: int) -> None:
    shelves = [s for s in world.shelves_in_zone(zone_id) if s.inventory]
    for i in range(count):
        shelf = shelves[i % len(shelves)]
        sku = next(iter(shelf.inventory))
        order = Order(
            id=f"ORD-T{i:04d}",
            created_tick=world.clock.tick,
            deadline_tick=world.clock.tick + 600,
            priority=OrderPriority.NORMAL,
            lines=[OrderLine(sku=sku, qty=1, shelf_id=shelf.id)],
        )
        apply(
            world, make_event(EventType.ORDER_CREATED, world.clock.tick, order.id, {"order": order.to_dict()})
        )


def test_congestion_forecast_hot_zone():
    world = build_world(spec_for("tiny", seed=7))
    _inject_orders_for_zone(world, "C", 20)
    forecasts = forecast_congestion(world, horizon_min=30)
    zone_c = next(c for c in forecasts if c.zone_id == "C")
    assert zone_c.projected_robots > zone_c.capacity
    assert zone_c.risk == "high" and zone_c.eta_min < 30
    assert any("20 open orders" in d for d in zone_c.drivers)
    assert forecasts[0].zone_id == "C"  # sorted by risk / load
    zone_a = next(c for c in forecasts if c.zone_id == "A")
    assert zone_a.risk == "low"
    assert all(c.zone_id not in ("CHG", "DOCK") for c in forecasts)


def test_bottlenecks_detect_failures():
    world = build_world(spec_for("tiny", seed=5))
    t = world.clock.tick
    apply(
        world, make_event(EventType.ROBOT_FAILURE, t, "R01", {"cause": "motor_fault", "recovery_ticks": 900})
    )
    apply(world, make_event(EventType.DOCK_CLOSED, t, "D1"))
    for cid in world.chargers:
        apply(world, make_event(EventType.CHARGER_DISABLED, t, cid))
    for r in world.robots.values():
        if r.status.operational:
            r.battery = 22.0
    demand = forecast_demand(world, None, horizon_min=60)
    battery = forecast_battery(world)
    congestion = forecast_congestion(world)
    found = detect_bottlenecks(world, demand, battery, congestion)
    kinds = {b.kind for b in found}
    assert {"robot", "dock", "charger"} <= kinds
    assert all(0.0 <= b.severity <= 1.0 and b.recommendation and b.message for b in found)
    assert found == sorted(found, key=lambda b: (-b.severity, b.kind, b.entity_id))
    robot = next(b for b in found if b.kind == "robot")
    assert robot.entity_id == "R01" and "motor_fault" in robot.message


def test_forecast_is_deterministic_and_fast():
    eng = make_engine("small", 42)
    rec = HistoryRecorder()
    eng.hooks.append(rec.hook)
    eng.run(1800)
    fc = Forecaster(horizon_min=90)
    t0 = time.perf_counter()
    a = fc.forecast(eng.world, rec, eng.pathfinder)
    elapsed = time.perf_counter() - t0
    b = fc.forecast(eng.world, rec, eng.pathfinder)
    assert a.model_dump() == b.model_dump()
    assert elapsed < 0.5
    assert a.generated_tick == 1800 and a.summary and a.demand.expected_orders > 0
    assert len(a.battery) == len(eng.world.robots)
    assert len(a.congestion) == len(eng.world.storage_zones()) + len(eng.world.corridor_zones())
    q = fc.quick(eng.world, rec)
    assert q.demand.expected_orders == a.demand.expected_orders and q.summary
