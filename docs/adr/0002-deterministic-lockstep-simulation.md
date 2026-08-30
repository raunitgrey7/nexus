# ADR 0002 — Deterministic lockstep simulation

**Status:** accepted (2026-08-30)

## Context

Simulate-before-execute is only meaningful if a simulation of "the same world" evolves exactly as the live world would;
benchmarks are only comparable if every strategy sees identical orders and incidents; replay is only useful if it
reproduces state exactly.

## Decision

Simulated time is an integer tick (`SimClock`); all randomness flows through one `SeededRNG` inside the world; the tick's
order of operations is fixed and robots advance in id order; ids come from counters in the world; solvers are configured
deterministically (single-worker CP-SAT with a fixed seed, seeded GA). Only *external* inputs are logged; everything the
engine produces is regenerated on replay (the lockstep model used by multiplayer game engines).

## Consequences

* `WorldState.digest()` makes determinism a unit test: same seed ⇒ same digest; fork ⇒ identical continuation; replay ⇒
  identical digest.
* Stability analysis is cheap: derive new random streams from the world's seed and compare outcomes.
* No wall-clock or thread scheduling may influence the engine; the live loop only decides *how often* to step.
* Floating-point paths are avoided in kinematics (movement is a tick-skip rule, actions use absolute ticks) so results
  are identical across platforms.
