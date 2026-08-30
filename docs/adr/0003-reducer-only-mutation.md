# ADR 0003 — Only the reducer mutates the world

**Status:** accepted (2026-08-30)

## Context

With many components (engine, strategies, agents, executors, scenario injection) touching the same world, ad-hoc
mutations make invariants impossible to guarantee and replays impossible to trust.

## Decision

`nexus/events/reducer.py: apply(world, event)` is the only code path that mutates a `WorldState`. Engines and agents
*emit events* (`SimulationEngine.emit` / `inject`); reducer handlers are small, total (they skip stale state rather than
raise), free of I/O and randomness, and increment `world.version`. Derived caches (occupancy, zone occupancy, open-order
index) are maintained inside the reducer's helpers (`WorldState.place_robot`, `mark_order_status`).

## Consequences

* Invariants live in one place; new state changes require a new `EventType` plus a handler and a test.
* Engine code stays pure: it reads state and decides, which made the engine trivially forkable and picklable.
* Transient kinematic state was redesigned to avoid per-tick scratch mutations (absolute `action_until_tick`,
  deterministic tick-skip instead of fractional accumulators).
* Slight verbosity: even a battery top-up during charging is an (ephemeral) event.
