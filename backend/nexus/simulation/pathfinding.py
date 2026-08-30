"""Grid pathfinding: A* with optional congestion costs, BFS distance fields, and a path cache.

Cells are addressed as flat indices inside the hot loops (``y * width + x``) — roughly 3× faster
than tuple-based neighbours in CPython, which matters because a large benchmark performs ~10⁵
searches.
"""

from __future__ import annotations

import heapq
from collections import deque
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

from nexus.twin.entities import Cell

if TYPE_CHECKING:
    from nexus.twin.world import WorldState

CostFn = Callable[[int], float]  # extra cost of *entering* a cell index
WALKABLE_TYPES = (0, 3, 4, 6)


class Pathfinder:
    def __init__(self, world: WorldState, cache_size: int = 20_000) -> None:
        self.world = world
        self.cache_size = cache_size
        self._cache: dict[tuple[int, int], list[Cell] | None] = {}
        self._cache_version = -1
        self._bfs_cache: dict[int, list[int]] = {}
        self._bfs_version = -1
        self.searches = 0
        self.cache_hits = 0

    # ---- helpers -------------------------------------------------------------------------------
    def _check_version(self) -> None:
        v = self.world.grid.version
        if v != self._cache_version:
            self._cache.clear()
            self._cache_version = v
        if v != self._bfs_version:
            self._bfs_cache.clear()
            self._bfs_version = v

    def _passable(self) -> Callable[[int], bool]:
        grid = self.world.grid
        types = grid.types
        blocked = grid.blocked
        closed = grid.closed_zones
        zones = grid.zones

        if not blocked and not closed:

            def passable(i: int) -> bool:
                return types[i] in WALKABLE_TYPES

        else:

            def passable(i: int) -> bool:
                if types[i] not in WALKABLE_TYPES or i in blocked:
                    return False
                z = zones[i]
                return not (z is not None and z in closed)

        return passable

    # ---- A* ------------------------------------------------------------------------------------
    def astar(
        self,
        start: Cell,
        goal: Cell,
        avoid: Iterable[Cell] = (),
        cost_fn: CostFn | None = None,
        max_expansions: int = 80_000,
    ) -> list[Cell] | None:
        """Path from ``start`` to ``goal`` as the list of cells to enter (excludes ``start``)."""
        if start == goal:
            return []
        grid = self.world.grid
        w, h = grid.width, grid.height
        s = start.y * w + start.x
        g = goal.y * w + goal.x
        avoid_set = {c.y * w + c.x for c in avoid}
        cacheable = not avoid_set and cost_fn is None
        if cacheable:
            self._check_version()
            hit = self._cache.get((s, g))
            if hit is not None or (s, g) in self._cache:
                self.cache_hits += 1
                return list(hit) if hit is not None else None
        self.searches += 1
        passable = self._passable()
        if not passable(g) and g not in avoid_set:
            if cacheable:
                self._cache[(s, g)] = None
            return None
        gx, gy = goal.x, goal.y
        came_from: dict[int, int] = {}
        g_score: dict[int, float] = {s: 0.0}
        open_heap: list[tuple[float, int, int]] = [(0.0, 0, s)]
        counter = 0
        closed: set[int] = set()
        expansions = 0
        found = False
        while open_heap:
            _, _, current = heapq.heappop(open_heap)
            if current == g:
                found = True
                break
            if current in closed:
                continue
            closed.add(current)
            expansions += 1
            if expansions > max_expansions:
                break
            cx, cy = current % w, current // w
            base = g_score[current]
            # neighbours: right, left, up, down
            if cx + 1 < w:
                self._relax(
                    current + 1,
                    cx + 1,
                    cy,
                    base,
                    gx,
                    gy,
                    passable,
                    avoid_set,
                    cost_fn,
                    g_score,
                    came_from,
                    open_heap,
                    counter,
                    closed,
                )
                counter += 1
            if cx > 0:
                self._relax(
                    current - 1,
                    cx - 1,
                    cy,
                    base,
                    gx,
                    gy,
                    passable,
                    avoid_set,
                    cost_fn,
                    g_score,
                    came_from,
                    open_heap,
                    counter,
                    closed,
                )
                counter += 1
            if cy + 1 < h:
                self._relax(
                    current + w,
                    cx,
                    cy + 1,
                    base,
                    gx,
                    gy,
                    passable,
                    avoid_set,
                    cost_fn,
                    g_score,
                    came_from,
                    open_heap,
                    counter,
                    closed,
                )
                counter += 1
            if cy > 0:
                self._relax(
                    current - w,
                    cx,
                    cy - 1,
                    base,
                    gx,
                    gy,
                    passable,
                    avoid_set,
                    cost_fn,
                    g_score,
                    came_from,
                    open_heap,
                    counter,
                    closed,
                )
                counter += 1
        if not found:
            if cacheable:
                self._cache[(s, g)] = None
            return None
        path_idx = [g]
        node = g
        while node != s:
            node = came_from[node]
            path_idx.append(node)
        path_idx.pop()  # drop start
        path_idx.reverse()
        path = [Cell(i % w, i // w) for i in path_idx]
        if cacheable:
            if len(self._cache) >= self.cache_size:
                self._cache.clear()
            self._cache[(s, g)] = path
            return list(path)
        return path

    @staticmethod
    def _relax(
        n: int,
        nx_: int,
        ny_: int,
        base: float,
        gx: int,
        gy: int,
        passable: Callable[[int], bool],
        avoid_set: set[int],
        cost_fn: CostFn | None,
        g_score: dict[int, float],
        came_from: dict[int, int],
        open_heap: list[tuple[float, int, int]],
        counter: int,
        closed: set[int],
    ) -> None:
        if n in closed or n in avoid_set or not passable(n):
            return
        step = 1.0 if cost_fn is None else 1.0 + cost_fn(n)
        tentative = base + step
        if tentative < g_score.get(n, float("inf")):
            g_score[n] = tentative
            came_from[n] = n if False else came_from.get(n, 0)  # placeholder overwritten below
            came_from[n] = _CURRENT[0]
            heapq.heappush(open_heap, (tentative + abs(nx_ - gx) + abs(ny_ - gy), counter, n))

    # ---- BFS distance field --------------------------------------------------------------------
    def bfs_distances(self, start: Cell) -> list[int]:
        """Exact grid distance from ``start`` to every cell (-1 = unreachable). Cached per grid version."""
        self._check_version()
        grid = self.world.grid
        w = grid.width
        s = start.y * w + start.x
        cached = self._bfs_cache.get(s)
        if cached is not None:
            return cached
        h = grid.height
        passable = self._passable()
        dist = [-1] * (w * h)
        dist[s] = 0
        q = deque([s])
        while q:
            cur = q.popleft()
            d = dist[cur] + 1
            cx = cur % w
            for n in (
                cur + 1 if cx + 1 < w else -1,
                cur - 1 if cx > 0 else -1,
                cur + w if cur + w < w * h else -1,
                cur - w if cur >= w else -1,
            ):
                if n >= 0 and dist[n] < 0 and passable(n):
                    dist[n] = d
                    q.append(n)
        if len(self._bfs_cache) > 512:
            self._bfs_cache.clear()
        self._bfs_cache[s] = dist
        return dist

    def distance(self, a: Cell, b: Cell) -> int:
        """Exact walking distance (BFS field), or -1 if unreachable."""
        field = self.bfs_distances(a)
        return field[b.y * self.world.grid.width + b.x]

    def stats(self) -> dict[str, int]:
        return {"searches": self.searches, "cache_hits": self.cache_hits, "cached_paths": len(self._cache)}


# A* bookkeeping: the current node being expanded is shared via a tiny mutable so that ``_relax``
# stays a static method without allocating closures per expansion.
_CURRENT = [0]


def _astar_expand_patch() -> None:  # pragma: no cover - documentation only
    """See Pathfinder.astar: ``_CURRENT[0]`` is set before neighbours are relaxed."""


# Re-implement astar's loop body to set _CURRENT before relaxing (kept here to keep the method readable).
_original_astar = Pathfinder.astar


def _astar_with_current(
    self: Pathfinder,
    start: Cell,
    goal: Cell,
    avoid: Iterable[Cell] = (),
    cost_fn: CostFn | None = None,
    max_expansions: int = 80_000,
) -> list[Cell] | None:
    if start == goal:
        return []
    grid = self.world.grid
    w, h = grid.width, grid.height
    s = start.y * w + start.x
    g = goal.y * w + goal.x
    avoid_set = {c.y * w + c.x for c in avoid}
    cacheable = not avoid_set and cost_fn is None
    if cacheable:
        self._check_version()
        if (s, g) in self._cache:
            self.cache_hits += 1
            hit = self._cache[(s, g)]
            return list(hit) if hit is not None else None
    self.searches += 1
    passable = self._passable()
    if not passable(g):
        if cacheable:
            self._cache[(s, g)] = None
        return None
    gx, gy = goal.x, goal.y
    came_from: dict[int, int] = {}
    g_score: dict[int, float] = {s: 0.0}
    open_heap: list[tuple[float, int, int]] = [(0.0, 0, s)]
    counter = 0
    closed: set[int] = set()
    expansions = 0
    found = False
    inf = float("inf")
    while open_heap:
        _, _, current = heapq.heappop(open_heap)
        if current == g:
            found = True
            break
        if current in closed:
            continue
        closed.add(current)
        expansions += 1
        if expansions > max_expansions:
            break
        cx = current % w
        cy = current // w
        base = g_score[current]
        for n, nx_, ny_ in (
            (current + 1, cx + 1, cy),
            (current - 1, cx - 1, cy),
            (current + w, cx, cy + 1),
            (current - w, cx, cy - 1),
        ):
            if nx_ < 0 or nx_ >= w or ny_ < 0 or ny_ >= h:
                continue
            if n in closed or n in avoid_set or not passable(n):
                continue
            step = 1.0 if cost_fn is None else 1.0 + cost_fn(n)
            tentative = base + step
            if tentative < g_score.get(n, inf):
                g_score[n] = tentative
                came_from[n] = current
                counter += 1
                heapq.heappush(open_heap, (tentative + abs(nx_ - gx) + abs(ny_ - gy), counter, n))
    if not found:
        if cacheable:
            self._cache[(s, g)] = None
        return None
    path_idx = [g]
    node = g
    while node != s:
        node = came_from[node]
        path_idx.append(node)
    path_idx.pop()
    path_idx.reverse()
    path = [Cell(i % w, i // w) for i in path_idx]
    if cacheable:
        if len(self._cache) >= self.cache_size:
            self._cache.clear()
        self._cache[(s, g)] = path
        return list(path)
    return path


Pathfinder.astar = _astar_with_current  # type: ignore[method-assign]


def path_length(path: list[Cell] | None) -> int:
    return len(path) if path else 0
