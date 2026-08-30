"""Parametric warehouse layout generator.

Produces a complete :class:`~nexus.twin.world.WorldState` from a :class:`WarehouseSpec`. Layout::

    y ▲
      │ ┌──────┬──┬──────────┬──┬──────────┬──┐
      │ │ CHG  │C │  Zone E  │C │  Zone F  │C │   storage zones: 10×10, shelf strips with 1-cell aisles
      │ │      │  ├──────────┴──┴──────────┴──┤   corridors: 2 cells wide, own zones (C1, C2 …)
      │ │      │  │        corridor            │   charging bay: left strip
      │ │      │  ├──────────┬──┬──────────┬──┤   loading docks: bottom strip
      │ │      │  │  Zone A  │C │  Zone B  │C │
      │ ├──────┴──┴──────────┴──┴──────────┴──┤
      │ │             DOCKS / STAGING          │
      └─┴──────────────────────────────────────┴──▶ x

Three built-in scales (``small`` / ``medium`` / ``large``) match the benchmark tiers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from nexus.core.clock import SimClock
from nexus.core.ids import IdGen, zone_letter
from nexus.core.rng import SeededRNG
from nexus.twin.entities import (
    Cell,
    CellType,
    ChargingStation,
    DemandProfile,
    LoadingDock,
    Robot,
    Shelf,
    SimConfig,
    Worker,
    Zone,
    ZoneKind,
)
from nexus.twin.spatial import GridMap
from nexus.twin.world import WorldState

CHARGING_STRIP_WIDTH = 3
DOCK_STRIP_HEIGHT = 3
FIRST_NAMES = [
    "Aarav",
    "Priya",
    "Marcus",
    "Elena",
    "Kenji",
    "Sofia",
    "Diego",
    "Amara",
    "Liam",
    "Zara",
    "Noah",
    "Ines",
    "Ravi",
    "Mei",
    "Omar",
    "Hana",
    "Lucas",
    "Nia",
    "Ethan",
    "Leila",
    "Arjun",
    "Chloe",
    "Mateo",
    "Yuki",
    "Ivan",
    "Sana",
    "Felix",
    "Ada",
    "Tariq",
    "Maya",
]


@dataclass(slots=True)
class WarehouseSpec:
    name: str = "NEXUS Fulfillment Center"
    scale: str = "small"
    zones_x: int = 4
    zones_y: int = 3
    zone_width: int = 10
    zone_height: int = 10
    corridor_width: int = 2
    n_robots: int = 12
    n_workers: int = 7
    n_docks: int = 4
    n_chargers: int = 4
    charger_slots: int = 2
    n_skus: int = 600
    inventory_units: int = 18_000
    orders_per_hour: float = (
        400.0  # base rate; ≈ 4,000 orders per 10-hour operating day at the profile's mean
    )
    seed: int = 42
    skus_per_shelf: int = 3
    tick_seconds: int = 1
    robot_failure_rate_per_hour: float = 0.0

    @property
    def n_storage_zones(self) -> int:
        return self.zones_x * self.zones_y


SCALES: dict[str, WarehouseSpec] = {
    "tiny": WarehouseSpec(
        scale="tiny",
        zones_x=2,
        zones_y=2,
        n_robots=4,
        n_workers=3,
        n_docks=2,
        n_chargers=2,
        n_skus=120,
        inventory_units=4_000,
        orders_per_hour=90.0,
    ),
    "small": WarehouseSpec(scale="small"),
    "medium": WarehouseSpec(
        scale="medium",
        zones_x=6,
        zones_y=4,
        n_robots=40,
        n_workers=16,
        n_docks=8,
        n_chargers=10,
        n_skus=2_000,
        inventory_units=60_000,
        orders_per_hour=1_000.0,
    ),
    "large": WarehouseSpec(
        scale="large",
        zones_x=10,
        zones_y=5,
        n_robots=100,
        n_workers=30,
        n_docks=16,
        n_chargers=24,
        n_skus=5_000,
        inventory_units=150_000,
        orders_per_hour=1_800.0,  # ≈ 10,000 orders in a 6-hour window at the profile's mean
    ),
}


def spec_for(scale: str = "small", **overrides: object) -> WarehouseSpec:
    if scale not in SCALES:
        raise ValueError(f"unknown scale {scale!r}; choose one of {sorted(SCALES)}")
    return replace(SCALES[scale], **overrides)  # type: ignore[arg-type]


def build_world(spec: WarehouseSpec | None = None, **overrides: object) -> WorldState:
    """Build a fully populated warehouse world. Deterministic for a given spec."""
    spec = replace(spec or SCALES["small"], **overrides) if (spec or overrides) else SCALES["small"]  # type: ignore[arg-type]
    rng = SeededRNG(spec.seed)
    layout_rng = rng.derive(1)
    cw = spec.corridor_width
    zw, zh = spec.zone_width, spec.zone_height
    width = CHARGING_STRIP_WIDTH + cw + spec.zones_x * (zw + cw)
    height = DOCK_STRIP_HEIGHT + cw + spec.zones_y * (zh + cw)
    grid = GridMap(width, height)
    grid.fill(0, 0, width - 1, height - 1, CellType.FLOOR)

    world = WorldState(
        name=spec.name,
        domain="warehouse",
        seed=spec.seed,
        scale=spec.scale,
        grid=grid,
        clock=SimClock(tick_seconds=spec.tick_seconds),
        rng=rng,
        ids=IdGen(),
        demand=DemandProfile(orders_per_hour=spec.orders_per_hour),
        config=SimConfig(
            tick_seconds=spec.tick_seconds, robot_failure_rate_per_hour=spec.robot_failure_rate_per_hour
        ),
    )

    n_zones = spec.n_storage_zones
    n_corridors = (spec.zones_y + 1) + (spec.zones_x + 1)
    storage_capacity = max(3, round(2.0 * spec.n_robots / n_zones))
    corridor_capacity = max(4, round(3.0 * spec.n_robots / n_corridors))

    # ---- charging bay (left strip) ---------------------------------------------------------------
    chg = Zone(
        "CHG",
        "Charging Bay",
        ZoneKind.CHARGING,
        0,
        0,
        CHARGING_STRIP_WIDTH - 1,
        height - 1,
        capacity=spec.n_robots + 2,
    )  # parking bay: never counts as congestion
    world.zones[chg.id] = chg
    grid.assign_zone(chg.x0, chg.y0, chg.x1, chg.y1, chg.id)
    usable_h = height - DOCK_STRIP_HEIGHT - 2
    for i in range(spec.n_chargers):
        y = DOCK_STRIP_HEIGHT + 1 + int((i + 0.5) * usable_h / spec.n_chargers)
        cell = Cell(0, min(height - 1, y))
        grid.set_type(cell.x, cell.y, CellType.CHARGER)
        cid = world.ids.next_short("CH", 2)
        world.chargers[cid] = ChargingStation(cid, cell, chg.id, slots=spec.charger_slots)

    # ---- docks (bottom strip) -----------------------------------------------------------------
    dock_zone = Zone(
        "DOCK",
        "Loading Docks",
        ZoneKind.DOCK,
        CHARGING_STRIP_WIDTH,
        0,
        width - 1,
        DOCK_STRIP_HEIGHT - 1,
        capacity=spec.n_docks * 3,
    )
    world.zones[dock_zone.id] = dock_zone
    grid.assign_zone(dock_zone.x0, dock_zone.y0, dock_zone.x1, dock_zone.y1, dock_zone.id)
    grid.fill(dock_zone.x0, 1, dock_zone.x1, DOCK_STRIP_HEIGHT - 1, CellType.STAGING)
    usable_w = width - CHARGING_STRIP_WIDTH - cw
    for i in range(spec.n_docks):
        x = CHARGING_STRIP_WIDTH + cw + int((i + 0.5) * usable_w / spec.n_docks)
        cell = Cell(min(width - 1, x), 0)
        grid.set_type(cell.x, cell.y, CellType.DOCK)
        did = world.ids.next_short("D", 1)
        world.docks[did] = LoadingDock(did, cell, dock_zone.id)

    # ---- corridors ----------------------------------------------------------------------------
    corridor_n = 0
    # horizontal corridors (one below each zone row, plus one above the top row)
    for row in range(spec.zones_y + 1):
        y0 = DOCK_STRIP_HEIGHT + row * (zh + cw)
        corridor_n += 1
        cid = f"C{corridor_n}"
        zone = Zone(
            cid,
            f"Corridor {corridor_n}",
            ZoneKind.CORRIDOR,
            CHARGING_STRIP_WIDTH,
            y0,
            width - 1,
            y0 + cw - 1,
            capacity=corridor_capacity,
        )
        world.zones[cid] = zone
        grid.assign_zone(zone.x0, zone.y0, zone.x1, zone.y1, cid)
    # vertical corridors (one left of each zone column, plus one at the right edge)
    for col in range(spec.zones_x + 1):
        x0 = CHARGING_STRIP_WIDTH + col * (zw + cw)
        corridor_n += 1
        cid = f"C{corridor_n}"
        zone = Zone(
            cid,
            f"Corridor {corridor_n}",
            ZoneKind.CORRIDOR,
            x0,
            DOCK_STRIP_HEIGHT,
            x0 + cw - 1,
            height - 1,
            capacity=corridor_capacity,
        )
        world.zones[cid] = zone
        # vertical corridors are assigned cell-by-cell so they do not overwrite horizontal ones
        for y in range(zone.y0, zone.y1 + 1):
            for x in range(zone.x0, zone.x1 + 1):
                if grid.zone_of(x, y) is None or grid.zone_of(x, y) == chg.id:
                    grid.assign_zone(x, y, x, y, cid)
        if cid not in grid.zone_ids:
            grid.zone_ids.append(cid)

    # ---- storage zones ------------------------------------------------------------------------
    shelf_cells: list[tuple[str, int, Cell, Cell]] = []
    for row in range(spec.zones_y):
        for col in range(spec.zones_x):
            index = row * spec.zones_x + col
            zid = zone_letter(index)
            x0 = CHARGING_STRIP_WIDTH + cw + col * (zw + cw)
            y0 = DOCK_STRIP_HEIGHT + cw + row * (zh + cw)
            zone = Zone(
                zid,
                f"Zone {zid}",
                ZoneKind.STORAGE,
                x0,
                y0,
                x0 + zw - 1,
                y0 + zh - 1,
                capacity=storage_capacity,
            )
            world.zones[zid] = zone
            grid.assign_zone(zone.x0, zone.y0, zone.x1, zone.y1, zid)
            k = 0
            for ly in range(1, zh - 1):
                for lx in range(zw):
                    if lx % 3 == 0:
                        continue  # aisle
                    k += 1
                    cell = Cell(x0 + lx, y0 + ly)
                    grid.set_type(cell.x, cell.y, CellType.SHELF)
                    access = Cell(cell.x - 1, cell.y) if lx % 3 == 1 else Cell(cell.x + 1, cell.y)
                    shelf_cells.append((zid, k, cell, access))
    for zid, k, cell, access in shelf_cells:
        sid = f"{zid}-{k:02d}"
        world.shelves[sid] = Shelf(sid, cell, access, zid)

    # ---- SKUs + inventory (Zipf popularity → natural hot zones) ---------------------------------
    shelf_ids = list(world.shelves)
    skus = [f"SKU-{i:05d}" for i in range(1, spec.n_skus + 1)]
    popularity = [1.0 / (i + 1) ** 0.8 for i in range(spec.n_skus)]
    total_pop = sum(popularity)
    world.sku_popularity = {sku: p / total_pop for sku, p in zip(skus, popularity, strict=True)}
    layout_rng.shuffle(shelf_ids)
    shelf_cursor = 0
    for sku in skus:
        copies = 1 + (1 if layout_rng.chance(0.35) else 0) + (1 if layout_rng.chance(0.15) else 0)
        # stock proportional to popularity (Zipf) so fast movers do not starve, split across copies
        units_total = max(4 * copies, round(spec.inventory_units * world.sku_popularity[sku]))
        placed = 0
        for _ in range(copies):
            shelf = world.shelves[shelf_ids[shelf_cursor % len(shelf_ids)]]
            shelf_cursor += 1
            if len(shelf.inventory) >= spec.skus_per_shelf:
                continue
            shelf.inventory[sku] = max(2, units_total // copies)
            world.sku_index.setdefault(sku, []).append(shelf.id)
            placed += 1
        if placed == 0:  # every SKU must live somewhere
            shelf = min(world.shelves.values(), key=lambda sh: (len(sh.inventory), sh.id))
            shelf.inventory[sku] = max(2, units_total)
            world.sku_index.setdefault(sku, []).append(shelf.id)
    # top up sparse shelves so the floor looks (and behaves) stocked
    filler_units = max(2, spec.inventory_units // (len(shelf_ids) * spec.skus_per_shelf * 4))
    for shelf in world.shelves.values():
        while len(shelf.inventory) < spec.skus_per_shelf:
            sku = layout_rng.choice(skus)
            if sku in shelf.inventory:
                continue
            shelf.inventory[sku] = filler_units + layout_rng.randint(0, 3)
            world.sku_index.setdefault(sku, []).append(shelf.id)

    # ---- robots (start in the charging bay, staggered batteries) -------------------------------
    bay_cells = [Cell(x, y) for y in range(DOCK_STRIP_HEIGHT, height) for x in range(1, CHARGING_STRIP_WIDTH)]
    for i in range(spec.n_robots):
        rid = f"R{i + 1:02d}"
        cell = bay_cells[i % len(bay_cells)]
        robot = Robot(
            rid,
            cell,
            chg.id,
            battery=round(layout_rng.uniform(65.0, 100.0), 1),
            speed=world.config.robot_speed,
        )
        world.robots[rid] = robot
    # ---- workers ------------------------------------------------------------------------------
    roles = ["loader"] * max(1, spec.n_docks // 2) + ["packer"] * 2 + ["picker", "supervisor"]
    dock_list = list(world.docks.values())
    for i in range(spec.n_workers):
        role = roles[i % len(roles)]
        wid = f"W{i + 1:02d}"
        dock = dock_list[i % len(dock_list)]
        cell = Cell(dock.cell.x, min(DOCK_STRIP_HEIGHT - 1, dock.cell.y + 1))
        world.workers[wid] = Worker(wid, FIRST_NAMES[i % len(FIRST_NAMES)], role, cell, dock_zone.id)

    world.rebuild_caches()
    return world


def estimate_layout(spec: WarehouseSpec) -> dict[str, int]:
    cw = spec.corridor_width
    width = CHARGING_STRIP_WIDTH + cw + spec.zones_x * (spec.zone_width + cw)
    height = DOCK_STRIP_HEIGHT + cw + spec.zones_y * (spec.zone_height + cw)
    shelves_per_zone = (spec.zone_height - 2) * math.floor(spec.zone_width * 2 / 3)
    return {"width": width, "height": height, "shelves": shelves_per_zone * spec.n_storage_zones}
