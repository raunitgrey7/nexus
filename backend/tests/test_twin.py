import itertools

from nexus.core.ids import IdGen, zone_letter
from nexus.core.rng import SeededRNG
from nexus.twin import SCALES, SpatialGraph, WorldState, build_world, get_domain, spec_for
from nexus.twin.entities import Cell, CellType, ZoneKind


def test_zone_letters():
    assert [zone_letter(i) for i in (0, 1, 25, 26, 27, 51, 52)] == ["A", "B", "Z", "AA", "AB", "AZ", "BA"]


def test_idgen_is_deterministic_and_copyable():
    g = IdGen()
    assert g.next("ORD") == "ORD-000001"
    assert g.next("ORD") == "ORD-000002"
    c = g.copy()
    assert c.next("ORD") == g.next("ORD")


def test_rng_fork_and_poisson():
    r = SeededRNG(3)
    values = [r.random() for _ in range(5)]
    f = SeededRNG(3)
    assert [f.random() for _ in range(5)] == values
    fork = r.fork()
    assert [fork.random() for _ in range(3)] == [r.random() for _ in range(3)]
    big = SeededRNG(1)
    mean = sum(big.poisson(4.0) for _ in range(5000)) / 5000
    assert 3.7 < mean < 4.3
    mean_large = sum(big.poisson(60.0) for _ in range(2000)) / 2000
    assert 56 < mean_large < 64


def test_all_scales_build_with_layout_invariants():
    for scale in SCALES:
        world = build_world(spec_for(scale))
        grid = world.grid
        assert len(world.robots) == SCALES[scale].n_robots
        assert len(world.docks) == SCALES[scale].n_docks
        assert len(world.chargers) == SCALES[scale].n_chargers
        # every cell belongs to exactly one zone
        assert all(z is not None for z in grid.zones)
        # every shelf has a walkable access cell adjacent to it
        for shelf in world.shelves.values():
            assert grid.cell_type(*shelf.cell) == CellType.SHELF
            assert grid.walkable(*shelf.access_cell)
            assert shelf.cell.manhattan(shelf.access_cell) == 1
            assert shelf.inventory, "shelves are stocked"
        for dock in world.docks.values():
            assert grid.walkable(*dock.cell)
        for charger in world.chargers.values():
            assert grid.walkable(*charger.cell)
        for robot in world.robots.values():
            assert grid.walkable(*robot.cell)
        # storage zones are only adjacent to corridors
        adj = world.zone_adjacency()
        for zone in world.storage_zones():
            assert adj[zone.id], f"{zone.id} isolated"
            for other in adj[zone.id]:
                assert world.zones[other].kind == ZoneKind.CORRIDOR
        # adjacency symmetric
        for a, bs in adj.items():
            for b in bs:
                assert a in adj[b]
        assert world.inventory_units() > 0
        assert set(world.sku_popularity) == set(world.sku_index)


def test_digest_is_deterministic_and_sensitive(small_world):
    again = build_world(spec_for("small", seed=42))
    assert small_world.digest() == again.digest()
    other = build_world(spec_for("small", seed=43))
    assert small_world.digest() != other.digest()
    robot = next(iter(small_world.robots.values()))
    robot.battery -= 1.0
    assert small_world.digest() != again.digest()


def test_fork_is_isolated(small_world):
    fork = small_world.fork("x")
    assert fork.is_fork and not small_world.is_fork
    assert fork.digest() == small_world.digest()
    r = next(iter(fork.robots.values()))
    fork.place_robot(r, Cell(r.cell.x + 1, r.cell.y))
    assert fork.digest() != small_world.digest()
    assert fork.rng.random() == small_world.rng.random()  # same RNG stream continues in both


def test_snapshot_round_trip(small_world):
    data = small_world.snapshot_bytes()
    restored = WorldState.from_snapshot(data)
    assert restored.digest() == small_world.digest()
    assert restored.zone_occupancy == small_world.zone_occupancy


def test_spatial_graph_queries(small_world):
    sg = SpatialGraph(small_world)
    assert sg.graph.number_of_nodes() > 500
    assert set(sg.adjacent_zones("A")) == small_world.zone_adjacency()["A"]
    route = sg.zone_route("A", "L")
    assert route and route[0] == "A" and route[-1] == "L"
    for a, b in itertools.pairwise(route):
        assert b in sg.adjacent_zones(a)
    assert "A" in sg.entities_in_zone("A", "shelf")[0]
    load = sg.zone_load()
    assert load["CHG"]["robots"] == len(small_world.robots)
    small_world.zones["C1"].closed = True
    small_world.grid.close_zone("C1")
    sg2 = SpatialGraph(small_world)
    assert "C1" not in sg2.adjacent_zones("A")


def test_grid_walkability_and_blocking(tiny_world):
    grid = tiny_world.grid
    cell = next(c for c in grid.walkable_cells() if grid.cell_type(*c) == CellType.FLOOR)
    assert grid.walkable(*cell)
    v = grid.version
    grid.block(cell)
    assert not grid.walkable(*cell) and grid.version == v + 1
    grid.unblock(cell)
    assert grid.walkable(*cell)
    grid.close_zone("A")
    zone = tiny_world.zones["A"]
    assert not grid.walkable(zone.x0, zone.y0)
    grid.open_zone("A")
    assert grid.walkable(zone.x0, zone.y0)


def test_domain_registry():
    dom = get_domain("warehouse")
    world = dom.build("tiny", seed=1)
    assert world.domain == "warehouse"
    assert "robots" in dom.describe(world)
    assert dom.vocabulary()["agent"] == "robot"


def test_world_to_dict_shapes(tiny_world):
    d = tiny_world.to_dict()
    assert d["grid"]["width"] == tiny_world.grid.width
    assert len(d["grid"]["rows"]) == tiny_world.grid.height
    assert {"summary", "robots", "zones", "shelves", "docks", "chargers", "orders", "tasks", "stats"} <= set(
        d
    )
