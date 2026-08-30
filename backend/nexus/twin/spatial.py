"""Spatial model: occupancy grid + semantic spatial graph.

Two complementary views of space:

* :class:`GridMap` — a dense cell grid (cell types, zone membership, dynamic blockages). This is what
  pathfinding and kinematics use. It is deliberately primitive (``bytearray``) because it is copied on
  every world fork.
* :class:`SpatialGraph` — a *semantic* graph (``R7 is_inside Zone C``, ``Zone C adjacent_to Zone D``,
  ``Order 8821 requires Shelf C-41``). It is derived from the world on demand and used by the agents,
  the risk checks and the natural-language explanations. This is the "spatial AI" layer: reasoning
  about relationships, not just coordinates.
"""

from __future__ import annotations

import hashlib
from collections import deque
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

import networkx as nx

from nexus.twin.entities import Cell, CellType

if TYPE_CHECKING:
    from nexus.twin.world import WorldState

NEIGHBOR_OFFSETS = ((1, 0), (-1, 0), (0, 1), (0, -1))


class GridMap:
    __slots__ = ("blocked", "closed_zones", "height", "types", "version", "width", "zone_ids", "zones")

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.version = 0  # bumped on every walkability change (pathfinding cache key)
        self.types = bytearray(width * height)  # CellType values, row-major
        self.zones: list[str | None] = [None] * (width * height)
        self.zone_ids: list[str] = []
        self.blocked: set[int] = set()  # dynamically blocked cell indices (aisle blockages)
        self.closed_zones: set[str] = set()

    # ---- indexing ------------------------------------------------------------------------------
    def idx(self, x: int, y: int) -> int:
        return y * self.width + x

    def cell_of(self, index: int) -> Cell:
        return Cell(index % self.width, index // self.width)

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    # ---- cell types ----------------------------------------------------------------------------
    def cell_type(self, x: int, y: int) -> CellType:
        return CellType(self.types[y * self.width + x])

    def set_type(self, x: int, y: int, cell_type: CellType) -> None:
        self.types[y * self.width + x] = int(cell_type)

    def fill(self, x0: int, y0: int, x1: int, y1: int, cell_type: CellType) -> None:
        for y in range(y0, y1 + 1):
            row = y * self.width
            for x in range(x0, x1 + 1):
                self.types[row + x] = int(cell_type)

    # ---- zones ---------------------------------------------------------------------------------
    def assign_zone(self, x0: int, y0: int, x1: int, y1: int, zone_id: str) -> None:
        if zone_id not in self.zone_ids:
            self.zone_ids.append(zone_id)
        for y in range(y0, y1 + 1):
            row = y * self.width
            for x in range(x0, x1 + 1):
                self.zones[row + x] = zone_id

    def zone_of(self, x: int, y: int) -> str | None:
        if not self.in_bounds(x, y):
            return None
        return self.zones[y * self.width + x]

    # ---- walkability ---------------------------------------------------------------------------
    def walkable(self, x: int, y: int) -> bool:
        if not (0 <= x < self.width and 0 <= y < self.height):
            return False
        i = y * self.width + x
        if self.types[i] not in (0, 3, 4, 6):  # FLOOR, DOCK, CHARGER, STAGING
            return False
        if i in self.blocked:
            return False
        zone = self.zones[i]
        return not (zone is not None and zone in self.closed_zones)

    def neighbors(self, cell: Cell) -> list[Cell]:
        out = []
        for dx, dy in NEIGHBOR_OFFSETS:
            nx_, ny_ = cell.x + dx, cell.y + dy
            if self.walkable(nx_, ny_):
                out.append(Cell(nx_, ny_))
        return out

    def block(self, cell: Cell) -> None:
        self.blocked.add(self.idx(cell.x, cell.y))
        self.version += 1

    def unblock(self, cell: Cell) -> None:
        self.blocked.discard(self.idx(cell.x, cell.y))
        self.version += 1

    def close_zone(self, zone_id: str) -> None:
        self.closed_zones.add(zone_id)
        self.version += 1

    def open_zone(self, zone_id: str) -> None:
        self.closed_zones.discard(zone_id)
        self.version += 1

    def is_blocked(self, cell: Cell) -> bool:
        return self.idx(cell.x, cell.y) in self.blocked

    def walkable_cells(self) -> list[Cell]:
        return [
            self.cell_of(i)
            for i in range(self.width * self.height)
            if self.walkable(i % self.width, i // self.width)
        ]

    def cells_of_type(self, cell_type: CellType) -> list[Cell]:
        value = int(cell_type)
        return [self.cell_of(i) for i, t in enumerate(self.types) if t == value]

    def nearest_walkable(self, cell: Cell, max_radius: int = 6) -> Cell | None:
        if self.walkable(cell.x, cell.y):
            return cell
        seen = {cell}
        frontier = deque([cell])
        while frontier:
            current = frontier.popleft()
            if current.manhattan(cell) > max_radius:
                continue
            for dx, dy in NEIGHBOR_OFFSETS:
                nxt = Cell(current.x + dx, current.y + dy)
                if nxt in seen or not self.in_bounds(nxt.x, nxt.y):
                    continue
                seen.add(nxt)
                if self.walkable(nxt.x, nxt.y):
                    return nxt
                frontier.append(nxt)
        return None

    # ---- zone adjacency (static geometry, dynamic closures handled by callers) ------------------
    def zone_adjacency(self) -> dict[str, set[str]]:
        adj: dict[str, set[str]] = {z: set() for z in self.zone_ids}
        w, h = self.width, self.height
        for y in range(h):
            for x in range(w):
                i = y * w + x
                if self.types[i] not in (0, 3, 4, 6):
                    continue
                za = self.zones[i]
                if za is None:
                    continue
                for dx, dy in ((1, 0), (0, 1)):
                    nx_, ny_ = x + dx, y + dy
                    if nx_ >= w or ny_ >= h:
                        continue
                    j = ny_ * w + nx_
                    if self.types[j] not in (0, 3, 4, 6):
                        continue
                    zb = self.zones[j]
                    if zb is None or zb == za:
                        continue
                    adj[za].add(zb)
                    adj[zb].add(za)
        return adj

    # ---- serialization -------------------------------------------------------------------------
    def rows(self) -> list[str]:
        """Compact textual encoding, one string per row (cell type digits)."""
        return [
            "".join(str(t) for t in self.types[y * self.width : (y + 1) * self.width])
            for y in range(self.height)
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "rows": self.rows(),
            "blocked": [list(self.cell_of(i)) for i in sorted(self.blocked)],
            "closed_zones": sorted(self.closed_zones),
        }

    def digest(self) -> str:
        h = hashlib.sha256()
        h.update(bytes(self.types))
        h.update(",".join(str(i) for i in sorted(self.blocked)).encode())
        h.update(",".join(sorted(self.closed_zones)).encode())
        return h.hexdigest()


class SpatialGraph:
    """Semantic spatial graph derived from a world state.

    Relations (edge attribute ``rel``):
    ``adjacent_to`` zone↔zone · ``located_in`` shelf/dock/charger→zone · ``is_inside`` robot/worker→zone ·
    ``assigned_to`` task→robot · ``serves`` task→order · ``requires`` order→shelf · ``requires_zone`` order→zone ·
    ``charging_at`` robot→charger · ``unloading_at`` robot→dock
    """

    def __init__(self, world: WorldState) -> None:
        self.world = world
        self.graph = nx.DiGraph()
        self._build()

    # ---- construction --------------------------------------------------------------------------
    def _build(self) -> None:
        g = self.graph
        w = self.world
        for zone in w.zones.values():
            g.add_node(zone.id, kind="zone", name=zone.name, zone_kind=zone.kind.value, closed=zone.closed)
        for a, neighbors in w.zone_adjacency().items():
            for b in neighbors:
                g.add_edge(a, b, rel="adjacent_to")
        for shelf in w.shelves.values():
            g.add_node(shelf.id, kind="shelf")
            g.add_edge(shelf.id, shelf.zone_id, rel="located_in")
        for dock in w.docks.values():
            g.add_node(dock.id, kind="dock", open=dock.open)
            g.add_edge(dock.id, dock.zone_id, rel="located_in")
        for charger in w.chargers.values():
            g.add_node(charger.id, kind="charger", enabled=charger.enabled)
            g.add_edge(charger.id, charger.zone_id, rel="located_in")
        for robot in w.robots.values():
            g.add_node(robot.id, kind="robot", status=robot.status.value, battery=robot.battery)
            g.add_edge(robot.id, robot.zone_id, rel="is_inside")
            if robot.charger_id:
                g.add_edge(robot.id, robot.charger_id, rel="charging_at")
        for worker in w.workers.values():
            g.add_node(worker.id, kind="worker", role=worker.role, status=worker.status.value)
            g.add_edge(worker.id, worker.zone_id, rel="is_inside")
        for task in w.tasks.values():
            if task.status.value not in ("planned", "active"):
                continue
            g.add_node(task.id, kind="task", status=task.status.value)
            g.add_edge(task.id, task.robot_id, rel="assigned_to")
            for oid in task.order_ids:
                g.add_edge(task.id, oid, rel="serves")
        for order in w.orders.values():
            if not order.status.open:
                continue
            g.add_node(order.id, kind="order", status=order.status.value, priority=order.priority.name)
            zones_needed: set[str] = set()
            for line in order.lines:
                sh = w.shelves.get(line.shelf_id)
                if sh is None:
                    continue
                g.add_edge(order.id, sh.id, rel="requires")
                zones_needed.add(sh.zone_id)
            for z in zones_needed:
                g.add_edge(order.id, z, rel="requires_zone")
            if order.dock_id:
                g.add_edge(order.id, order.dock_id, rel="ships_from")

    # ---- queries -------------------------------------------------------------------------------
    def relations_of(self, entity_id: str) -> list[tuple[str, str, str]]:
        """All (subject, relation, object) triples touching ``entity_id``."""
        triples = [(entity_id, d["rel"], v) for _, v, d in self.graph.out_edges(entity_id, data=True)]
        triples += [(u, d["rel"], entity_id) for u, _, d in self.graph.in_edges(entity_id, data=True)]
        return triples

    def adjacent_zones(self, zone_id: str, include_closed: bool = False) -> list[str]:
        out = []
        for _, v, d in self.graph.out_edges(zone_id, data=True):
            if d.get("rel") != "adjacent_to":
                continue
            if not include_closed and self.graph.nodes[v].get("closed"):
                continue
            out.append(v)
        return sorted(out)

    def entities_in_zone(self, zone_id: str, kind: str | None = None) -> list[str]:
        out = []
        for u, _, d in self.graph.in_edges(zone_id, data=True):
            if d.get("rel") not in ("is_inside", "located_in"):
                continue
            if kind is None or self.graph.nodes[u].get("kind") == kind:
                out.append(u)
        return sorted(out)

    def orders_requiring_zone(self, zone_id: str) -> list[str]:
        return sorted(
            u for u, _, d in self.graph.in_edges(zone_id, data=True) if d.get("rel") == "requires_zone"
        )

    def zone_route(self, from_zone: str, to_zone: str, avoid: Iterable[str] = ()) -> list[str] | None:
        """Shortest zone-level route (BFS over adjacency), honouring closures and ``avoid``."""
        avoid_set = set(avoid)
        if from_zone == to_zone:
            return [from_zone]
        prev: dict[str, str | None] = {from_zone: None}
        queue = deque([from_zone])
        while queue:
            current = queue.popleft()
            for nxt in self.adjacent_zones(current):
                if nxt in prev or nxt in avoid_set:
                    continue
                prev[nxt] = current
                if nxt == to_zone:
                    path = [nxt]
                    while prev[path[-1]] is not None:
                        path.append(prev[path[-1]])  # type: ignore[arg-type]
                    return list(reversed(path))
                queue.append(nxt)
        return None

    def zone_load(self) -> dict[str, dict[str, int]]:
        """Per zone: robots inside, open orders requiring it, shelves."""
        result: dict[str, dict[str, int]] = {}
        for zone_id in self.world.zones:
            result[zone_id] = {
                "robots": len(self.entities_in_zone(zone_id, "robot")),
                "workers": len(self.entities_in_zone(zone_id, "worker")),
                "orders_requiring": len(self.orders_requiring_zone(zone_id)),
                "shelves": len(self.entities_in_zone(zone_id, "shelf")),
            }
        return result

    def describe(self, entity_id: str, limit: int = 12) -> list[str]:
        """Human-readable triples, used in LLM prompts and explanations."""
        lines = [f"{s} {r} {o}" for s, r, o in self.relations_of(entity_id)]
        return lines[:limit]

    def to_dict(self, max_nodes: int = 800) -> dict[str, Any]:
        nodes = []
        for n, d in list(self.graph.nodes(data=True))[:max_nodes]:
            nodes.append({"id": n, **d})
        keep = {n["id"] for n in nodes}
        edges = [
            {"source": u, "target": v, "rel": d["rel"]}
            for u, v, d in self.graph.edges(data=True)
            if u in keep and v in keep
        ]
        return {"nodes": nodes, "edges": edges}
