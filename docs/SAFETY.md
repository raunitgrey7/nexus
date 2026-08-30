# Safety architecture

NEXUS can recommend and execute actions against an operation, so safety is a first-class design constraint rather than a
feature. The controls stack up: a **closed action vocabulary** the LLM cannot escape, **constraint validation** that
drops or clamps anything unsafe, **simulate-before-execute** in a forked world with the real engine, a **risk agent**
that judges deadlocks, safety violations, resource exhaustion, regressions and *stability* across seeds, an **approval
policy** that keeps a human in the loop for anything but low-risk, clearly beneficial plans, and **attributable,
idempotent events** for everything that reaches the live world. This document lists the rules and thresholds as
implemented and closes with an honest statement of limitations.

## 1. Closed action vocabulary

Plans are lists of `ActionModel(type, params, rationale)` where `type` is one of sixteen literal values
(`nexus/api/schemas.py: ActionType`). The planner prompt (`nexus/llm/prompts.py`) lists the vocabulary and the parameter
shapes; anything else the model emits is discarded before validation. There is no free-form "run this command" action.

## 2. Constraint validation (`nexus/agents/validator.py`)

| Action | Rule |
|---|---|
| `REASSIGN_TASKS` | robot ids must exist (targets must be operational), zone ids must exist; `max_tasks` clamped to 1–60; dropped if nothing valid remains |
| `REPRIORITIZE_ORDERS` | priority must be `LOW/NORMAL/HIGH/CRITICAL`; zones filtered to known ids; `boost_minutes` clamped to 0–15 |
| `SEND_TO_CHARGE` | only operational, not-charging robots; **at most a quarter of the fleet** (excess trimmed and reported) |
| `REROUTE_AVOID_ZONE` | known zones only; `penalty` 0.5–10; `duration_min` 5–240 |
| `PREFER_CORRIDOR` | corridor zones only; `bonus` 0.05–0.8; `duration_min` 5–240 |
| `REPOSITION_INVENTORY` | distinct, existing zones; target must be open; `skus` 1–12, `units` 5–200 |
| `SET_BATCHING` | `orders_per_trip` clamped to 1–4 |
| `SET_ZONE_CAPACITY` | known zones; capacities 1–30 |
| `CLOSE_ZONE` | storage zones only, and **at least one storage zone must stay open** |
| `ADD_ROBOTS` | 1–4 per plan |
| `REMOVE_ROBOTS` | known ids, **never more than a third of the fleet** |
| `DISPATCH_WORKER` | worker and dock must exist |
| `CANCEL_TASKS` | known task ids |
| `SET_STRATEGY` | one of the registered strategies |
| anything else | dropped |

A plan left with only `NOOP` is marked infeasible (unless it *is* the "Do nothing" reference). Errors are kept on the plan
(`validation_errors`) and surface as an `info` risk finding, so operators see what was clamped.

The optimizer applies its own feasibility constraints when it builds tasks (`nexus/optimization/constraints.py`): robot
capacity, battery with reserve, sourceable lines, open dock, one robot per task and one task per order.

## 3. Simulate before execute

No plan touches the live world without a forked-world simulation over the decision horizon (`NEXUS_SIM_HORIZON_TICKS`,
default 90 minutes). The simulation uses the *same* engine, scheduler (a pickled clone) and pending scheduled faults as
the live twin, so the projected KPIs are what the twin itself would produce. The "Do nothing" reference is always
simulated alongside, and a plan is only recommended if its score beats the reference.

## 4. Risk assessment (`nexus/agents/risk.py`)

| Finding | Trigger | Severity |
|---|---|---|
| `constraint` | plan infeasible / actions dropped by validation | critical / info |
| `deadlock` | max robot wait ≥ 60 s or > 600 stuck robot-seconds · ≥ 25 s or > 150 | high · medium |
| `safety` | a zone reaches ≥ 2.0× capacity · ≥ 1.5× | high · low |
| `resource_exhaustion` | min battery < 5 % · < 12 % · robots starved of chargers > 120 robot-seconds · stock-outs · failures appearing in simulation | high · medium · medium · low · medium |
| `regression` | SLA breach worse than doing nothing by > 0.5 pp · throughput −5 % · congestion +50 % (and > 0.2) | high · medium · low |
| `capacity` | utilization > 97 % (no slack) | low |
| `instability` | σ of projected SLA breach across stability seeds > 5 % · > 2.5 % · else | high · medium · info |

Score = min(1, Σ weights) with info 0, low 0.12, medium 0.30, high 0.55, critical 1.0. Level: `CRITICAL` if any critical
finding or score ≥ 0.75; `HIGH` if any high finding or score ≥ 0.45; `MEDIUM` if score ≥ 0.2; else `LOW`.

**Stability re-runs.** The recommended plan is re-simulated `NEXUS_RISK_SEEDS` (default 2) more times under random
streams derived from the world's seed (`rng.derive(17·i)`), and the spread of the SLA outcome is reported
(`stability.sla_breach_std` …). A plan whose benefit disappears under a different order sequence is flagged.

## 5. Approval policy (`nexus/agents/policy.py`)

* "Do nothing" is auto-approved (there is nothing to execute).
* Any other plan must have been simulated and risk-assessed.
* Auto-approval requires **both** `risk.level ≤ NEXUS_AUTO_APPROVE_MAX_RISK` (default `LOW`) **and** a projected
  SLA-breach improvement ≥ `NEXUS_AUTO_APPROVE_MIN_GAIN` (default 0.02 = 2 percentage points).
* Otherwise the decision stays `proposed` with `policy="human"` and a stated reason; an operator approves
  (`POST /api/decisions/{id}/actions {"action": "approve"}`, optionally choosing another candidate via `plan_id`) or
  rejects it. `execute` refuses decisions that are not `approved` (HTTP 409).

Auto-approval **can** release a low-risk, clearly beneficial plan (typically reassignment, routing, batching,
prioritisation, pre-emptive charging). It **cannot** bypass validation, skip simulation, or execute a plan that scores
worse than doing nothing; and it never applies to `MEDIUM`+ risk.

## 6. Attributable, idempotent events

Every executed action becomes ordinary events with `origin="agent"`, `cause=<plan id>` and an idempotency `key`
(`"{plan}:{action}:{n}:{type}"`). The `EventStore` rejects duplicate keys, so a retried or re-delivered execution is a
no-op; `PLAN_PROPOSED / PLAN_APPROVED / PLAN_REJECTED / PLAN_EXECUTED` bracket the decision in the same log; with
`NEXUS_DATABASE_URL` the log, snapshots and decision records are persisted for audit. The replay machinery
(`nexus/events/replay.py`) can reconstruct any state from a snapshot and the external events after it.

## 7. Engine-level safeguards

* Robots never enter closed zones or blocked cells; paths through them are dropped and re-planned.
* Zone capacities are *soft* (they slow robots and penalise routing) but the risk agent treats ≥ 2× capacity as a
  safety violation.
* Battery depletion is modelled as a failure with a recovery time, so plans that starve robots of charge pay for it in
  the simulation and in the `resource_exhaustion` finding.
* Scheduled faults and injected events are idempotent by key; scenario mutations never leak into the live world (they
  are applied to forks only).

## 8. Failure modes and mitigations

| Failure mode | Mitigation |
|---|---|
| LLM unavailable / slow / invalid JSON | availability probe with cache, bounded timeouts, one schema-guided retry, deterministic playbooks always present |
| LLM hallucinates entity ids or unsafe parameters | validator drops unknown ids and clamps ranges; the closed vocabulary prevents unknown actions |
| Simulation too slow for the incident | horizon and candidate count are configurable; jobs run in a process pool; the twin keeps ticking during decisions |
| A plan looks good on one seed only | stability re-runs and the `instability` finding |
| Operator approves a stale decision | execution applies to the *current* world through the same validated executor; idempotency keys prevent double application |
| Process pool cannot start (restricted environments) | automatic fallback to sequential in-process simulation |
| Database or Redis down | the twin runs fully in memory; persistence is additive |

## 9. Limitations (read before trusting the numbers)

* **Simulation fidelity.** Robots are point agents on a grid with cell capacities, deterministic slow-downs and a simple
  battery model; there is no physics, no perception, no real-world latency. The `simulation fidelity` KPI in
  `ROADMAP.md` (|simulated − realized|) can only be measured against a real system; today the twin validates plans
  against *itself*.
* **No real robots.** Execution means emitting events into the twin. Integrating a fleet manager, Webots/Gazebo/Isaac Sim
  or a WMS is roadmap work (`README.md → Roadmap`), and the approval policy should default to `human` in any such
  integration until fidelity is established.
* **Single-process engine.** The live twin is one Python process; parallelism comes from evaluating forks. Very large
  sites (thousands of robots) would need a different engine core.
* **Heuristic playbooks encode warehouse assumptions.** They are correct for the shipped warehouse domain; new domains
  need their own playbooks and SOPs (`docs/DOMAIN_EXTENSION.md`).
* **No authentication in the API.** Put it behind your gateway before exposing it (`SECURITY.md`).
