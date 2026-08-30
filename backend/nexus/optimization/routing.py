"""Congestion-aware routing policy.

A :class:`RoutingPolicy` turns *intent* ("avoid Zone C for 30 minutes", "prefer corridor 4") and
*live state* (zone occupancy vs capacity) into a per-cell extra cost consumed by the A* search::

    extra(cell) = penalty(zone)  −  min(0.9, bonus(zone))  +  k · max(0, occupancy − capacity) / capacity

Penalties and bonuses expire at ``until_tick``. The bonus is floored at −0.9 so that every step
still costs > 0 (A* remains correct; the Manhattan heuristic merely stops being admissible for
bonus cells, which is acceptable for a preference). The policy is plain data — picklable — because
strategies carrying it are simulated inside forked worlds and worker processes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from nexus.twin.entities import ZoneKind

if TYPE_CHECKING:
    from nexus.twin.world import WorldState

_TRAFFIC_ZONES = frozenset({ZoneKind.STORAGE, ZoneKind.CORRIDOR})


def zone_cell_index(world: WorldState) -> dict[str, list[int]]:
    """zone id → flat cell indices (walkable or not). Recompute when ``world.grid.version`` changes."""
    index: dict[str, list[int]] = {}
    for i, zone_id in enumerate(world.grid.zones):
        if zone_id is not None:
            index.setdefault(zone_id, []).append(i)
    return index


class CellCost:
    """Callable ``cell index → extra cost`` backed by a flat list (fast inside the A* loop)."""

    __slots__ = ("costs",)

    def __init__(self, costs: list[float]) -> None:
        self.costs = costs

    def __call__(self, index: int) -> float:
        return self.costs[index]


@dataclass
class RoutingPolicy:
    avoid_zones: dict[str, float] = field(default_factory=dict)  # zone id → penalty per cell
    prefer_corridors: dict[str, float] = field(default_factory=dict)  # zone id → bonus per cell (≤ 0.9)
    until_tick: dict[str, int] = field(default_factory=dict)  # zone id → expiry tick
    zone_capacity_override: dict[str, int] = field(default_factory=dict)
    congestion_weight: float = 1.5
    congestion_aware: bool = True

    # ---- mutation ------------------------------------------------------------------------------
    def avoid(self, zone_id: str, penalty: float, until_tick: int | None = None) -> None:
        self.avoid_zones[zone_id] = max(0.0, float(penalty))
        if until_tick is not None:
            self.until_tick[zone_id] = int(until_tick)

    def prefer(self, zone_id: str, bonus: float, until_tick: int | None = None) -> None:
        self.prefer_corridors[zone_id] = min(0.9, max(0.0, float(bonus)))
        if until_tick is not None:
            self.until_tick[zone_id] = int(until_tick)

    def clear(self) -> None:
        self.avoid_zones.clear()
        self.prefer_corridors.clear()
        self.until_tick.clear()
        self.zone_capacity_override.clear()

    def expire(self, tick: int) -> list[str]:
        expired = [z for z, t in self.until_tick.items() if t <= tick]
        for z in expired:
            self.until_tick.pop(z, None)
            self.avoid_zones.pop(z, None)
            self.prefer_corridors.pop(z, None)
            self.zone_capacity_override.pop(z, None)
        return expired

    # ---- queries -------------------------------------------------------------------------------
    @property
    def is_empty(self) -> bool:
        return not (self.avoid_zones or self.prefer_corridors or self.zone_capacity_override)

    def zone_costs(self, world: WorldState) -> dict[str, float]:
        """Non-zero extra cost per zone. The congestion term only applies to *traffic* zones
        (storage aisles and corridors) — robots parked at docks or chargers are not traffic."""
        costs: dict[str, float] = {}
        for zone in world.zones.values():
            c = self.avoid_zones.get(zone.id, 0.0) - min(
                0.9, max(0.0, self.prefer_corridors.get(zone.id, 0.0))
            )
            if self.congestion_aware and zone.kind in _TRAFFIC_ZONES:
                cap = self.zone_capacity_override.get(zone.id, zone.capacity)
                occ = world.zone_occupancy.get(zone.id, 0)
                if cap > 0 and occ > cap:
                    c += self.congestion_weight * (occ - cap) / cap
            if c:
                costs[zone.id] = round(c, 6)
        return costs

    def cost_fn(
        self,
        world: WorldState,
        zone_cells: dict[str, list[int]] | None = None,
        previous: tuple[dict[str, float], CellCost] | None = None,
    ) -> CellCost | None:
        """Per-cell extra cost for the current world, or ``None`` when routing is unaffected
        (which lets the pathfinder use its cache).

        ``zone_cells`` (see :func:`zone_cell_index`) lets the fill touch only the cells of zones
        with a non-zero cost; ``previous`` (zone costs, CellCost) is reused when nothing changed.
        """
        zone_costs = self.zone_costs(world)
        if not zone_costs:
            return None
        if previous is not None and previous[0] == zone_costs:
            return previous[1]
        n = world.grid.width * world.grid.height
        if zone_cells is None:
            zone_cells = zone_cell_index(world)
        costs = [0.0] * n
        for zone_id, c in zone_costs.items():
            for i in zone_cells.get(zone_id, ()):
                costs[i] = c
        return CellCost(costs)

    def describe(self) -> str:
        parts = []
        if self.avoid_zones:
            parts.append("avoid " + ", ".join(f"{z} (+{p:g})" for z, p in sorted(self.avoid_zones.items())))
        if self.prefer_corridors:
            parts.append(
                "prefer " + ", ".join(f"{z} (−{b:g})" for z, b in sorted(self.prefer_corridors.items()))
            )
        if self.zone_capacity_override:
            parts.append(
                "capacity " + ", ".join(f"{z}={c}" for z, c in sorted(self.zone_capacity_override.items()))
            )
        parts.append(
            f"congestion-aware (k={self.congestion_weight:g})" if self.congestion_aware else "static"
        )
        return "; ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "avoid_zones": dict(self.avoid_zones),
            "prefer_corridors": dict(self.prefer_corridors),
            "until_tick": dict(self.until_tick),
            "zone_capacity_override": dict(self.zone_capacity_override),
            "congestion_weight": self.congestion_weight,
            "congestion_aware": self.congestion_aware,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> RoutingPolicy:
        return RoutingPolicy(
            avoid_zones={str(k): float(v) for k, v in (d.get("avoid_zones") or {}).items()},
            prefer_corridors={str(k): float(v) for k, v in (d.get("prefer_corridors") or {}).items()},
            until_tick={str(k): int(v) for k, v in (d.get("until_tick") or {}).items()},
            zone_capacity_override={
                str(k): int(v) for k, v in (d.get("zone_capacity_override") or {}).items()
            },
            congestion_weight=float(d.get("congestion_weight", 1.5)),
            congestion_aware=bool(d.get("congestion_aware", True)),
        )
