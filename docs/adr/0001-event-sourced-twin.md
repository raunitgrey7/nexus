# ADR 0001 — The twin is event-sourced

**Status:** accepted (2026-08-30)

## Context

A digital twin must be auditable (what changed, when, why), reconstructible (rebuild any past state), and shareable with
observers (UI, metrics, persistence) without those observers being able to corrupt it. Mutable-object designs make all
three hard: state changes are scattered, history is lost, and every consumer needs its own lock discipline.

## Decision

Every change to the world is a typed `Event` (`nexus/events/types.py`) appended to an append-only `EventStore`, applied
by a pure reducer, and published on a bus. External commands carry an idempotency `key`; high-frequency kinematic events
are flagged *ephemeral* (streamed, not persisted). Snapshots (`WorldState.snapshot_bytes`) plus the external events after
them reproduce any state (`nexus/events/replay.py`).

## Consequences

* Full audit trail with `origin` and `cause` on every event; plans are attributable end to end.
* Replay and fork are first-class; determinism can be *tested* (`verify_replay`).
* Observers are pure subscribers (WebSocket bridge, Prometheus, persistence sink).
* Cost: every mutation goes through an event object and a handler (≈ 5–10 µs); the engine batches nothing, so large
  worlds pay for it in ticks/s. Ephemeral events keep the persisted log small.
