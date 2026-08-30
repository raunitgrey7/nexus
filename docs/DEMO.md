# Demo script (≈ 6 minutes)

This is the presentation storyline: a live digital twin, a deliberate incident, dozens of strategies simulated in
seconds, a recommended plan executed only after simulation and safety validation, and a what-if question answered in plain
language. Everything below works fully offline; Ollama only adds LLM-generated candidates and prose.

## Setup (before the audience arrives)

```bash
docker compose up --build -d          # or: make api (terminal 1) + make ui (terminal 2)
ollama pull qwen2.5:7b                # optional
```

Open `http://localhost:3000` (Live Twin), keep `http://localhost:8000/docs` and Grafana (`http://localhost:3001`) in other
tabs. Set the speed slider to ~100 ticks/s and let the twin run to about **10:30** simulated time (the late-morning ramp;
the clock is in the top bar). Then set speed to ~10 ticks/s so the audience can watch robots move.

## 0:00 — "This is a simulated warehouse. Everything you see is a live digital twin."

* Point at the 3D view: 12 robots, 12 storage zones, 4 docks, 4 charging stations, ~18,000 inventory units, ≈ 4,000
  orders a day. Orders flow, robots pick, deliver, charge.
* KPI bar: active orders, robots operational, projected SLA breach, throughput, utilization, congestion, system risk.
* Right panel → Events: every change is an event (`ORDER_CREATED`, `TASK_CREATED`, `ITEM_PICKED`, `CHARGING_STARTED`…).
  "The twin is a pure function of this log — we can replay, fork and hash it."

## 1:00 — Break something

* Fault panel → **Fail R07** (`ROBOT_FAILURE`, motor fault, 45 min recovery). R07 turns red; its tasks are released.
* "A normal system says: *Robot R07 offline*. Watch what NEXUS says instead."

## 1:30 — Decide

* Click **Decide now** (`POST /api/decisions`). While it runs (5–10 s): "Instead of reacting immediately, NEXUS forks the
  twin and simulates every candidate plan over the next 90 minutes — in parallel, with the real engine."
* Decisions page → the candidates table: *Do nothing* baseline vs. reassignment, prioritisation, corridor preference,
  batching, inventory repositioning… each with projected SLA breach bars, fulfillment, throughput, congestion, risk.
* Read the explanation aloud — it is generated from the record: *"R07 failure (motor fault) will increase average order
  fulfillment time by …% over the next 90 minutes (projected SLA breach …% without intervention). I evaluated N candidate
  plans in T s. Recommended plan #1 — …: reassign work from R07 to R03, R09 in zones B/C, prioritise high-priority orders
  and route through corridor C4. Estimated impact: SLA breach A → B … Risk LOW; auto-approved."*
* Open the risk report: deadlock, safety, resource, regression and **stability across seeds** findings.

## 3:00 — Simulate, approve, execute

* Show the timeline chart: baseline vs recommended plan over the horizon (breach and open orders).
* If the policy required a human: click **Approve** — "LOW risk with a ≥ 2-point gain is auto-approved; anything else
  waits for an operator." Then **Execute**.
* Back on the Live Twin: the plan lands as ordinary events (`PLAN_EXECUTED`, `TASK_CREATED` with `cause = plan id`).
  "Only after simulation and safety validation would this plan be eligible for execution against a real system."

## 4:00 — Ask it questions

* Console page → "Why are orders slowing down?" → *"Zone C congestion is currently the largest contributor, accounting for
  approximately …% of predicted delay …"* (attribution is a transparent decomposition, evidence shown beside the text).
* "What happens if tomorrow's order volume increases by 40%?" → the what-if runs the current strategy, `optimized` and
  `nexus_full` and answers with projected breach, capacity and the best strategy.

## 5:00 — What-If lab and benchmarks

* What-If page → preset **Remove 2 robots** (or **R07 fails during a demand spike**) → Run → grouped bars and timelines
  per strategy, best strategy badge, narrative.
* Benchmarks page → four strategies × three scales on identical worlds with the same incident schedule: baseline vs
  optimized vs ai_planner vs nexus_full (`docs/BENCHMARKS.md`).
* Close on Forecast (demand band, battery table, congestion bars, bottlenecks) and Grafana (planning latency, events/s).

## Terminal fallback (no browser)

```bash
cd backend
uv run nexus demo                              # the storyline: twin → R07 fails → candidates → best plan → executed
uv run nexus decide --scale small --warmup-min 60 --horizon-min 90 --candidates 8
uv run nexus whatif --preset demand-plus-40
uv run nexus run --scale small --minutes 120 --strategy optimized --fail-robot R07
```

## If Ollama is off

Nothing changes visibly except `LLM used` badges: the planner uses its deterministic playbooks, explanations and console
answers come from templates with the same numbers, and every run is reproducible. Say so — "the LLM proposes, the
mathematics disposes; the platform never depends on it."
