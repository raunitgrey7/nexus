# ADR 0006 — Simulation jobs run in a process pool

**Status:** accepted (2026-08-30)

## Context

The engine is pure Python and CPU-bound; threads do not parallelise it (GIL). A decision evaluates 6–10 candidates plus
stability re-runs; a what-if evaluates several strategies × seeds. Sequential evaluation would make the platform feel
slow exactly when it matters (during an incident).

## Decision

`nexus/agents/simulator.py` serialises each evaluation into a `SimJob` (world bytes, pickled strategy, plan dict,
horizon, seed salt, scheduled faults). `run_jobs` executes jobs in a persistent `ProcessPoolExecutor`
(`NEXUS_DECISION_WORKERS`) when there are at least three jobs, and falls back to in-process execution on any pool error.
Strategies that simulate internally (`nexus_full`) always run their own candidates in-process to avoid nested pools.

## Consequences

* Near-linear speed-up up to the core count; the live loop is never blocked by evaluation.
* Everything a job needs must be picklable: worlds already are; strategies drop engine references on pickle and rebuild
  them lazily; routing policies are plain data.
* Worker start-up costs ≈ 1 s on Windows (imports), amortised by keeping the pool alive.
* Restricted environments (no `spawn`) degrade gracefully to sequential execution.
