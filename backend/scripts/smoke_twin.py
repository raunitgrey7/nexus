"""Smoke test for the twin layer: build, render, fork, digest."""

import time

from nexus.twin import SpatialGraph, build_world, spec_for

GLYPH = {0: ".", 1: "#", 2: "X", 3: "D", 4: "C", 5: "=", 6: ","}


def render(world, max_rows=45):
    g = world.grid
    robots = {r.cell: r.id[-1] for r in world.robots.values()}
    rows = []
    for y in range(g.height - 1, -1, -1):
        row = []
        for x in range(g.width):
            c = (x, y)
            if c in robots:
                row.append("R")
            else:
                row.append(GLYPH[g.types[y * g.width + x]])
        rows.append("".join(row))
    return "\n".join(rows[:max_rows])


for scale in ("tiny", "small", "medium", "large"):
    t0 = time.perf_counter()
    w = build_world(spec_for(scale))
    dt = time.perf_counter() - t0
    s = w.summary()
    adj = w.zone_adjacency()
    t1 = time.perf_counter()
    f = w.fork()
    tf = time.perf_counter() - t1
    t2 = time.perf_counter()
    d1 = w.digest()
    td = time.perf_counter() - t2
    d2 = f.digest()
    w2 = build_world(spec_for(scale))
    print(
        f"{scale:6s} grid={w.grid.width}x{w.grid.height} zones={s['zones']} shelves={s['shelves']} inv={s['inventory_units']} robots={s['robots_total']} docks={len(w.docks)} chargers={len(w.chargers)} build={dt * 1000:.0f}ms fork={tf * 1000:.1f}ms digest={td * 1000:.1f}ms same_fork={d1 == d2} same_rebuild={d1 == w2.digest()} zoneA_adj={sorted(adj.get('A', []))}"
    )
w = build_world(spec_for("small"))
print(render(w))
sg = SpatialGraph(w)
print("graph nodes", sg.graph.number_of_nodes(), "edges", sg.graph.number_of_edges())
print("Zone C adjacent:", sg.adjacent_zones("C"))
print("route A->L:", sg.zone_route("A", "L"))
print("shelf sample:", next(iter(w.shelves.values())).to_dict())
print("sku index sample:", list(w.sku_index.items())[:2])
