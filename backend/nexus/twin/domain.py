"""Domain abstraction.

The engine (events, simulation, optimization, agents) is domain-agnostic: it only knows about the
entity *shapes* in :mod:`nexus.twin.entities`. A :class:`DomainModel` supplies the concrete world
(layout, demand, vocabulary) for a physical operation. ``warehouse`` is the first implementation;
``factory``/``hospital``/``airport`` plug in here without touching the engine.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Protocol, runtime_checkable

from nexus.twin.layout import SCALES, build_world
from nexus.twin.world import WorldState


@runtime_checkable
class DomainModel(Protocol):
    name: str
    scales: list[str]

    def build(self, scale: str = "small", seed: int = 42, **overrides: Any) -> WorldState: ...

    def vocabulary(self) -> dict[str, str]:
        """Maps generic engine concepts to domain wording (used in prompts, UI and explanations)."""
        ...

    def describe(self, world: WorldState) -> str:
        """Short natural-language description of the world for LLM context."""
        ...


class WarehouseDomain:
    name = "warehouse"
    scales = list(SCALES)

    def build(self, scale: str = "small", seed: int = 42, **overrides: Any) -> WorldState:
        spec = replace(SCALES[scale], seed=seed, **overrides)
        return build_world(spec)

    def vocabulary(self) -> dict[str, str]:
        return {
            "agent": "robot",
            "agents": "robots",
            "job": "order",
            "jobs": "orders",
            "resource": "shelf",
            "sink": "loading dock",
            "energy": "battery",
            "area": "zone",
            "operator": "worker",
        }

    def describe(self, world: WorldState) -> str:
        s = world.summary()
        return (
            f"{world.name}: {s['zones']} zones ({len(world.storage_zones())} storage, "
            f"{len(world.corridor_zones())} corridors), {s['shelves']} shelves holding {s['inventory_units']:,} units, "
            f"{s['robots_total']} robots ({s['robots_operational']} operational), {s['workers']} workers, "
            f"{len(world.docks)} loading docks, {len(world.chargers)} charging stations. "
            f"Demand ≈ {world.demand.orders_per_hour * world.demand.multiplier:.0f} orders/hour."
        )


DOMAINS: dict[str, DomainModel] = {"warehouse": WarehouseDomain()}


def get_domain(name: str = "warehouse") -> DomainModel:
    try:
        return DOMAINS[name]
    except KeyError as exc:
        raise ValueError(f"unknown domain {name!r}; registered: {sorted(DOMAINS)}") from exc
