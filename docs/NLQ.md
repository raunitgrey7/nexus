# Natural-language console

`nexus/nlq` lets an operator ask the twin questions in plain language — "Why are orders slowing down?", "What happens if
order volume increases by 40%?", "Which robot should charge next?" — and answers with numbers that come from the engine,
never from the model's imagination. Routing is deterministic (regex/keyword), parameters are extracted into typed
scenarios, delay attribution is a transparent weighted decomposition, and the LLM (when available) only rewrites a
grounded answer under a numbers-must-survive rule. This document describes the intents, the parameter extraction, the
attribution formula and the grounding rules.

## Intents (`nexus/nlq/router.py: classify`)

| Intent | Triggers (examples) | Handler |
|---|---|---|
| `whatif` | "what if", "what happens if", "suppose", "assume", "scenario", "if we …" | `build_scenario` → `WhatIfEngine.run` with the current strategy, `optimized` and `nexus_full` |
| `explain` | "why …", "what is causing", "root cause", "reason for"; also "slow", "delay", "late", "behind" | `attribute_delay` → `explain_text` |
| `recommend` | "should", "recommend", "what can we do", "best plan", "how do we fix/improve/reduce", "mitigate" | a full decision (`OperationsManager.decide`, ≤ 45 min horizon, 5 candidates) → its explanation |
| `forecast` | "forecast", "predict", "next hour / 30 min", "expect", "going to", "bottleneck", "upcoming" | `LiveRuntime.forecast(horizon)` → summary |
| `entity` | an id (`R07`, `ORD-000123`, `TASK-…`, `CH01`, `W03`, `D2`) or "where is", "status of", "tell me about", "details of" | entity lookup + `SpatialGraph.describe` relations |
| `status` | "how many", "status", "current", "right now", "kpi", "utilization", "throughput", "breach", "backlog", "overview" | KPIs + world summary |
| `unknown` | anything else | help text + suggestions; the LLM may refine the intent when reachable |

Order of precedence: what-if → explain → recommend → forecast → entity → status → (slow/delay → explain) → unknown.

## Parameter extraction (`extract_params`)

Regexes pull out percentages (`40%`), robot ids (`R7`/`R07` → `R07`), order/task ids, zone letters (`zone C`), docks
(`dock 2` → `D2`), chargers (`CH01`), workers (`W03`), minutes, robot counts (words and digits: "two robots"), and flags:
`multiplier` (double/twice → 2.0, triple → 3.0, half → 0.5, `±pct` with drop/decrease → below 1), `failure`, `remove`,
`add`, `closure`, `charging`, `demand`, `batching`, `worker_delay`, `aisle`, `move_inventory` (+ `from_zone`/`to_zone`).

`build_scenario(params, world)` maps them onto the scenario DSL (`docs/WHAT_IF.md`): robot failures, robot removals or
additions, demand multipliers or bursts (when minutes are given), zone or dock closures, blocked aisles, disabled chargers
("charging capacity halved" → half of the stations), worker delays, batching, inventory moves. Ids are checked against
the world; an unparseable hypothetical defaults to an R07 failure.

## Delay attribution (`nexus/nlq/explain.py: attribute_delay`)

Each observable cause gets a weight proportional to the open orders it is holding back; weights are normalised to shares
that sum to 100 %, so every percentage is traceable to entities in the twin:

| Cause | Weight |
|---|---|
| `zone_congestion` (per zone over capacity) | `(occupancy − capacity) · max(1, open orders needing the zone) · (1.0 storage / 0.6 corridor)` |
| `robot_unavailable` | `failed / fleet · open_orders · 1.2` |
| `charging` | `charging / fleet · open_orders · 0.6` |
| `capacity_backlog` (pending orders and no free robot) | `pending · 0.8` |
| `zone_closed` | `open orders needing closed zones · 1.5 + 1` |
| `aisle_blocked` | `blocked_cells · 0.5 + replans · 0.02` |
| `dock_closed` | `closed / docks · open_orders · 0.5` |
| `worker_delay` (unavailable loaders) | `delayed_loaders · 2.0` |
| `demand_pressure` (utilization > 85 %) | `(utilization − 0.85) · 40 + 1` |

`explain_text` renders the primary cause — *"Zone C congestion is currently the largest contributor, accounting for
approximately 61% of predicted delay (Zone C: 5/3 robots, 14 open orders need it)."* — the next two contributors, and the
current numbers (open/pending orders, projected breach, p95 fulfillment, utilization). When nothing material is found it
says so.

## Grounding rules for the LLM rewrite (`nexus/nlq/service.py`)

* The grounded answer is computed first and is authoritative; the LLM receives it together with a compacted JSON of the
  supporting data and is asked to rewrite it "keeping every number".
* Rewrites are only attempted for `explain`, `forecast`, `status`, `entity` and `whatif`; `recommend` answers are the
  decision explanation (already number-checked in `nexus/agents/explain.py`).
* A rewrite is discarded if it is more than 4× longer than the grounded answer; when the LLM is unavailable
  (`NullLLM`, Ollama down, `use_llm=false`) the grounded answer is returned unchanged with `llm_used=false`.
* `NLQResponse` always returns `intent`, the structured `data` used (attribution, forecast, what-if result, decision id,
  entity + relations) and `latency_ms`, so the UI can render the evidence next to the prose.

Suggested prompts (`SUGGESTIONS`): "Why are orders slowing down?", "What happens if order volume increases by 40%?",
"What if robot R07 fails right now?", "What if we remove two robots?", "Which robot should charge next?", "What should we
do about zone C congestion?", "Forecast the next 60 minutes", "Where is R03 and what is it doing?".
