"""Replay: rebuild a world from a snapshot plus the external events recorded after it.

Engine-produced events are *not* replayed — the deterministic engine regenerates them. Only external
inputs (scenario faults, agent plans, user commands) are re-injected at the tick they originally
happened. This is the lockstep-simulation model: small logs, exact reconstruction.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

from nexus.events.types import Event
from nexus.twin.world import WorldState

if TYPE_CHECKING:
    from nexus.simulation.engine import SimulationEngine

EngineFactory = Callable[[WorldState], "SimulationEngine"]


def replay(
    snapshot: bytes | WorldState,
    external_events: Iterable[Event],
    engine_factory: EngineFactory,
    until_tick: int,
) -> SimulationEngine:
    world = snapshot.fork() if isinstance(snapshot, WorldState) else WorldState.from_snapshot(snapshot)
    engine = engine_factory(world)
    pending = sorted(
        (e for e in external_events if e.tick >= world.clock.tick), key=lambda e: (e.tick, e.seq)
    )
    i = 0
    while world.clock.tick < until_tick:
        tick = world.clock.tick
        while i < len(pending) and pending[i].tick == tick:
            src = pending[i]
            engine.inject(src.type, src.entity_id, dict(src.payload), origin=src.origin, cause=src.cause)
            i += 1
        engine.step()
    return engine


def verify_replay(engine: SimulationEngine, snapshot: bytes, engine_factory: EngineFactory) -> bool:
    """Replays the engine's own history from ``snapshot`` and compares digests."""
    replayed = replay(snapshot, engine.store.external_events(), engine_factory, engine.world.clock.tick)
    return replayed.world.digest() == engine.world.digest()
