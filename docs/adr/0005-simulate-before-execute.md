# ADR 0005 — Simulate before execute

**Status:** accepted (2026-08-30)

## Context

Operational plans have second-order effects (reassigning robots creates congestion elsewhere; charging early creates
backlog). A recommendation engine that cannot quantify those effects before acting is a dashboard, not an autonomous
operations platform.

## Decision

No plan is executed on the live world without first being applied to a fork of the *current* world and simulated over
the decision horizon with the same engine, scheduler and pending faults; a "Do nothing" reference is always simulated
alongside; a plan is only recommended if its multi-objective score beats the reference; the recommended plan is
re-simulated under perturbed random streams to measure stability; the risk agent and the approval policy gate release.
The same executor applies plans to forks and to the live world, so the evaluation is faithful to the execution.

## Consequences

* Every decision carries projected KPIs, deltas versus doing nothing, diagnostics and a risk report — explanations are
  quantitative by construction.
* Decision latency is bounded by `candidates × horizon / workers` (seconds on a laptop for the small scale), so horizon
  and candidate count are configurable and simulation jobs run in a process pool (ADR 0006).
* Simulation fidelity becomes the central open question when integrating real systems (see `docs/SAFETY.md`
  → Limitations).
