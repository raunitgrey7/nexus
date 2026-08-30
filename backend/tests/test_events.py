import pytest

from nexus.events import DuplicateEventError, Event, EventBus, EventStore, EventType, apply, make_event
from nexus.simulation.order_generator import OrderGenerator
from nexus.simulation.tasks import make_task
from nexus.twin.entities import OrderStatus, RobotStatus, TaskStatus


def test_store_assigns_seq_and_persists_non_ephemeral():
    store = EventStore(ring_size=3)
    e1 = store.append(make_event(EventType.ORDER_CREATED, 1, "ORD-1", {"order": {}}))
    e2 = store.append(make_event(EventType.ROBOT_MOVED, 1, "R01", {"to": [1, 1]}))
    assert (e1.seq, e2.seq) == (1, 2)
    assert e1.id == "EVT-00000001"
    assert len(store) == 1 and store.log[0] is e1
    assert e2.ephemeral and e2 in store.recent
    for i in range(5):
        store.append(make_event(EventType.TICK, i))
    assert len(store.recent) == 3
    assert store.stats()["counts"]["TICK"] == 5


def test_store_idempotency_and_sinks():
    store = EventStore()
    seen = []
    store.add_sink(seen.append)
    store.append(make_event(EventType.ZONE_CLOSED, 5, "A", key="cmd-1", origin="user"))
    with pytest.raises(DuplicateEventError):
        store.append(make_event(EventType.ZONE_CLOSED, 6, "A", key="cmd-1", origin="user"))
    assert store.has_key("cmd-1") and len(seen) == 1
    assert store.external_events()[0].type == EventType.ZONE_CLOSED


def test_store_jsonl_round_trip():
    store = EventStore()
    store.append(make_event(EventType.ROBOT_FAILURE, 9, "R02", {"cause": "motor_fault"}, origin="scenario"))
    loaded = EventStore.load_jsonl(store.dump_jsonl())
    assert loaded[0].type == EventType.ROBOT_FAILURE and loaded[0].payload["cause"] == "motor_fault"
    assert loaded[0].external


def test_bus_subscribe_unsubscribe():
    bus = EventBus()
    got = []
    unsub = bus.subscribe(got.append, [EventType.TICK])
    all_got = []
    bus.subscribe(all_got.append)
    bus.publish(make_event(EventType.TICK, 1))
    bus.publish(make_event(EventType.ORDER_CREATED, 1))
    assert len(got) == 1 and len(all_got) == 2
    unsub()
    bus.publish(make_event(EventType.TICK, 2))
    assert len(got) == 1


def test_reducer_rejects_unknown(tiny_world):
    ev = Event(type=EventType.TICK, tick=0)
    ev.type = "NOPE"  # type: ignore[assignment]
    with pytest.raises(ValueError):
        apply(tiny_world, ev)


def test_reducer_order_task_lifecycle(tiny_world):
    world = tiny_world
    gen = OrderGenerator()
    order = gen.make_order(world)
    apply(world, make_event(EventType.ORDER_CREATED, 0, order.id, {"order": order.to_dict()}))
    assert world.orders[order.id].status == OrderStatus.PENDING
    assert world.stats.orders_created == 1
    robot = next(iter(world.robots.values()))
    task = make_task(world, robot, [world.orders[order.id]], check_battery=False)
    assert task is not None
    apply(world, make_event(EventType.TASK_CREATED, 0, robot.id, {"task": task.to_dict()}))
    assert robot.task_id == task.id and robot.status == RobotStatus.MOVING
    assert world.orders[order.id].status == OrderStatus.ASSIGNED
    assert world.orders[order.id].robot_id == robot.id
    # pick one line
    line = world.orders[order.id].lines[0]
    before = world.shelves[line.shelf_id].inventory[line.sku]
    apply(
        world,
        make_event(
            EventType.ITEM_PICKED,
            5,
            robot.id,
            {"order_id": order.id, "shelf_id": line.shelf_id, "sku": line.sku, "qty": line.qty},
        ),
    )
    assert world.shelves[line.shelf_id].inventory[line.sku] == before - line.qty
    assert line.picked and world.tasks[task.id].leg == 1
    assert world.orders[order.id].status == OrderStatus.IN_PROGRESS
    # failure releases the order and re-shelves picked stock
    apply(
        world,
        make_event(EventType.ROBOT_FAILURE, 6, robot.id, {"cause": "motor_fault", "recovery_ticks": 10}),
    )
    assert robot.status == RobotStatus.FAILED and robot.task_id is None
    assert world.tasks[task.id].status == TaskStatus.CANCELLED
    assert world.orders[order.id].status == OrderStatus.PENDING and not line.picked
    assert world.shelves[line.shelf_id].inventory[line.sku] == before
    assert world.stats.failures_total == 1
    apply(world, make_event(EventType.ROBOT_RECOVERED, 16, robot.id))
    assert robot.status == RobotStatus.IDLE


def test_reducer_infrastructure_events(tiny_world):
    world = tiny_world
    zone = world.storage_zones()[0]
    apply(world, make_event(EventType.ZONE_CLOSED, 1, zone.id, {"reason": "maintenance"}))
    assert zone.closed and not world.grid.walkable(zone.x0, zone.y0)
    apply(world, make_event(EventType.ZONE_OPENED, 2, zone.id))
    assert not zone.closed and world.grid.walkable(zone.x0, zone.y0)
    cell = world.shelves[next(iter(world.shelves))].access_cell
    apply(world, make_event(EventType.AISLE_BLOCKED, 3, None, {"cells": [list(cell)]}))
    assert not world.grid.walkable(*cell)
    apply(world, make_event(EventType.AISLE_CLEARED, 4, None, {"cells": [list(cell)]}))
    assert world.grid.walkable(*cell)
    dock = next(iter(world.docks.values()))
    apply(world, make_event(EventType.DOCK_CLOSED, 5, dock.id))
    assert not dock.open and dock not in world.open_docks()
    charger = next(iter(world.chargers.values()))
    apply(world, make_event(EventType.CHARGER_DISABLED, 6, charger.id))
    assert charger.free_slots == 0
    apply(
        world,
        make_event(
            EventType.DEMAND_CHANGED,
            7,
            None,
            {"multiplier": 1.4, "burst_multiplier": 2.0, "burst_ticks": 100},
        ),
    )
    assert world.demand.multiplier == 1.4 and world.demand.burst_until_tick == 107
    apply(
        world,
        make_event(
            EventType.CONFIG_CHANGED,
            8,
            None,
            {"batch_max_orders": 3, "sla_minutes": {"NORMAL": 8}, "capacities": {zone.id: 1}},
        ),
    )
    assert (
        world.config.batch_max_orders == 3
        and world.config.sla_minutes["NORMAL"] == 8.0
        and zone.capacity == 1
    )


def test_reducer_inventory_move(tiny_world):
    world = tiny_world
    src = next(s for s in world.shelves.values() if s.inventory)
    sku, qty = next(iter(src.inventory.items()))
    dst = next(s for s in world.shelves.values() if s.id != src.id)
    apply(
        world,
        make_event(
            EventType.INVENTORY_MOVED,
            1,
            None,
            {"sku": sku, "from_shelf": src.id, "to_shelf": dst.id, "qty": qty},
        ),
    )
    assert sku not in src.inventory and dst.inventory[sku] >= qty
    assert dst.id in world.sku_index[sku] and src.id not in world.sku_index[sku]


def test_reducer_robot_add_remove(tiny_world):
    world = tiny_world
    n = len(world.robots)
    cell = list(world.robots["R01"].cell)
    apply(
        world,
        make_event(
            EventType.ROBOT_ADDED,
            1,
            "R99",
            {"robot": {"id": "R99", "cell": cell, "zone_id": "CHG", "battery": 80}},
        ),
    )
    assert len(world.robots) == n + 1 and "R99" in world.occupancy[world.robots["R99"].cell]
    apply(world, make_event(EventType.ROBOT_REMOVED, 2, "R99"))
    assert len(world.robots) == n and "R99" not in world.robots
