# NEXUS — The Pitch

> A virtual world that mirrors a physical operation, plus AI agents that understand, predict,
> **simulate** and optimize what happens inside it — and never touch the real world without
> simulating the action first.

This file is a **speaking script**, not a spec. Three versions of the pitch (30 seconds, 3 minutes,
10-minute demo), the numbers to memorize, who to sell to, the objections you will get, and the exact
words to close with. Deliver it with the live app open: **https://nexus-twin.vercel.app** (or
`make up` locally, or `uv run nexus demo` in a terminal if the network fails you).

---

## 1. The hook (memorize this)

> "When a robot fails in a warehouse today, the software says: *'Robot R07 offline.'*
> NEXUS says: *'R07's failure will push average fulfillment time from 2 to 9 minutes and SLA breach
> to 39% within the hour. I evaluated 9 recovery plans and 47 task reallocations in 19 seconds —
> simulated, not guessed. The best one brings breach to 3%. Risk is LOW and stable across seeds.
> Approved and executing.'*
>
> That's the difference between a dashboard and an operations brain."

---

## 2. The 30-second elevator pitch (verbatim)

> "NEXUS is an AI-native digital twin for physical operations — warehouses first. It keeps a live,
> event-sourced model of the floor: every robot, order, shelf and worker. On top of it, a team of AI
> agents forecasts what's coming, proposes recovery plans when things break, **simulates every plan
> in a forked copy of the world**, scores them with an optimization engine, risk-checks them, and
> only then executes — with a human in the loop where it matters. On our reproducible benchmark, a
> naive scheduler breaches SLA on 42% of orders during a bad morning; the full NEXUS loop holds it
> at 1.8%. It runs entirely local-first — no cloud APIs, no per-token costs — and the engine is
> domain-agnostic: the warehouse is just the first world."

---

## 3. The 3-minute pitch (verbatim, with beats)

**Beat 1 — the problem (30 s)**

> "Physical operations run blind between reports. WMS and dashboards tell you *what happened*;
> nobody tells the shift manager *what is about to happen* or *which of their five possible
> reactions actually works*. So when demand surges or a robot dies at 10:30 on a Monday, people
> react on instinct, and the cost shows up as missed SLAs, overtime and idle hardware. Simulation
> tools exist, but they live in engineering teams and take weeks per question — they're not in the
> control room when the incident happens."

**Beat 2 — what NEXUS is (30 s)**

> "NEXUS puts a live digital twin *and* a planning brain in the control room. The twin is
> event-sourced — every change to the world is a typed, replayable event — and the simulation is
> deterministic: same seed, same events, bit-identical world. That's not an implementation detail;
> it's what makes the next part trustworthy: any moment can be **forked** and every 'what if' can be
> answered by actually running the future, at hundreds of times real speed."

**Beat 3 — how the brain works (45 s)**

> "When something breaks — or when you just ask — a pipeline runs: a Forecaster projects demand,
> battery and congestion; a Planner proposes diverse recovery plans — an LLM proposes some, and
> deterministic playbooks guarantee coverage, so the system works with the LLM switched off; a
> validator enforces a closed action vocabulary; an optimization engine — CP-SAT, the same class of
> solver that schedules airlines — turns intentions into concrete task assignments; then every
> candidate plan is simulated in its own forked world, a risk agent checks for deadlocks, safety and
> stability across random seeds, and a policy decides: auto-approve, or hand to a human. The one
> rule that never bends: **nothing executes that wasn't simulated first.**"

**Beat 4 — proof (30 s)**

> "We benchmark, we don't demo-ware. Same worlds, same seeds, same incident script — robot failure,
> demand surge, blocked aisle — four strategies. Naive scheduling breaches 42% of SLAs; classical
> optimization alone gets to about 3%; the full agentic loop gets to **1.8%** while *raising*
> throughput. At 100 robots and ten thousand orders the engine still simulates at roughly half a
> million sim-seconds per wall-clock hour. Every number regenerates from one command."

**Beat 5 — why now, and the ask (45 s)**

> "Physical AI is the next platform shift — analysts have moved digital twins, multi-agent systems
> and spatial intelligence to the top of every 2026 trend list, and warehouse automation is already
> a multi-billion market growing double digits. But the intelligence layer is missing: robot vendors
> ship fleet managers, WMS vendors ship records, twin vendors ship 3D viewers. Decision-making is
> still human-only. NEXUS is that missing layer, and because the engine never imports the warehouse,
> the same core extends to factories, hospitals, airports and data centers.
> *(investor)* I'm raising to take this from a proven engine to two lighthouse pilots.
> *(customer)* I'm looking for one site and ninety days to prove double-digit SLA improvement
> against your own event data — simulation only, zero risk to operations.
> *(hiring)* This is solo work, twenty-six thousand lines, production discipline — I'd do the same
> for your systems."

---

## 4. The 10-minute demo talk track

Open **https://nexus-twin.vercel.app** (Live Twin page). Speed slider to ~50× beforehand so the
floor is busy. Script per click:

| # | You do | You say |
|---|--------|---------|
| 1 | Point at the 3D floor | "Everything you see is a live digital twin — 12 robots, 12 storage zones, 576 shelves, real orders flowing. This isn't a video; the engine is ticking on the server and streaming state over a WebSocket." |
| 2 | Point at KPI bar | "The KPIs are computed from the same definitions everywhere — live view, benchmarks, and the investor deck. Watch 'Predicted SLA breach' — that number is a projection, not history." |
| 3 | Point at event feed | "Every change is an event — typed, attributable, replayable. `TASK_CREATED method=cpsat` means the constraint solver just assigned a batch of orders to a robot." |
| 4 | Click **Demand burst ×2 (30 min)** | "It's 10:30 on a Monday and marketing just ran a flash sale." |
| 5 | Click **Robot R07 fails** | "And now our best robot dies. A normal system's contribution ends here: 'R07 offline.' Watch what the twin does instead — 11 of 12, backlog building, predicted breach climbing." |
| 6 | Click **Decide now** | "NEXUS won't react on instinct. It's forecasting the next 90 minutes, generating candidate plans, and — this is the part I care about — **simulating each one in a forked copy of this exact world**. Nine plans, forty-seven task reallocations, about twenty seconds." |
| 7 | Decision drawer opens; point at the candidates table | "Every row is a *simulated future*, not a score from a heuristic. 'Do nothing' projects 39% breach. Reassignment alone barely helps — the optimizer already reassigns. The winner adds two reserve robots and batching: 3% breach, throughput up 25%. And see the risk column — LOW, stable across three random seeds. Plans that only work on one seed are not plans." |
| 8 | Point at approval line | "Policy gate: LOW risk plus a real improvement auto-approves; anything else waits for a human. Every executed action lands in the event log with the plan that caused it — full audit trail." |
| 9 | Click **Approve/Execute** (if not auto) | "Executing against the live twin. In a real site this is the point where it becomes a work order to your WMS or fleet manager — same events, different sink." |
| 10 | Go to **What-If** page, run "Demand +40%" | "Same machinery answers planning questions before you spend money: what if volume grows 40%? Under the naive scheduler you'd breach 20%+; under NEXUS you're fine until here — so you know *when* to buy robot #13, not guess." |
| 11 | Go to **Console**, ask "Why are orders slowing down?" | "Operators don't write SQL. The console decomposes delay causally — 'Zone C congestion contributes ~60%' — from the twin's state, and the LLM only *phrases* it. Numbers never come from the model." |
| 12 | Go to **Benchmarks** page | "And this is the honesty slide: four strategies, identical worlds, incident script included. 42% → 1.8%. Reproduce it with one command." |

**Fallbacks:** deployed Space asleep → it wakes on first request (~1 min), narrate slide 6 of the
deck meanwhile. Total outage → `uv run nexus demo` in a terminal tells the same story in ASCII.

---

## 5. Numbers to memorize

| Number | What it is |
|---|---|
| **41.7% → 2.8% → 1.8%** | SLA breach, small scale: baseline → optimized → full NEXUS loop (120 min, 3 seeds, incidents) |
| **37.6% → 1.5%** | Same at 100 robots / 50 zones / ~10k orders (large) |
| **39.4% → 3.1%** | The live demo decision: do-nothing projection vs. recommended plan |
| **9 plans / 47 allocations / ~19 s** | One decision cycle, simulated in parallel forked worlds |
| **~6,000 ticks/s** | Small-world simulation speed (≈ 100 minutes of warehouse per wall-clock second) |
| **1.8 ms / 26 ms** | World fork time, small / large — forking futures is cheap by design |
| **96 tests · bit-identical replay** | Same seed + same events ⇒ same world hash, enforced in CI |
| **0 API keys** | Local LLM (Ollama) with deterministic fallback; runs air-gapped |

---

## 6. Target market — who buys this, and who to call

### Customer segments (in order of attack)

1. **Mid-market fulfillment & 3PL warehouses** (5–100k orders/day, some automation, no simulation
   team). *Buyer:* COO / VP Operations / Head of Fulfillment. *User:* shift manager, ops planner.
   *Pain:* SLA penalties, peak-season firefighting, "how many robots do we actually need?"
   *Entry:* What-If capacity studies — pure simulation, zero operational risk.
2. **AMR / warehouse-robotics vendors & integrators.** *Buyer:* VP Product / Head of Deployments.
   *Pain:* they sell robots with fleet managers, but customers ask "what happens if…" during every
   sales cycle and every incident. *Entry:* white-label NEXUS as their ops-intelligence layer;
   the simulate-before-execute story also de-risks their autonomy roadmap.
3. **WMS / supply-chain software vendors.** *Buyer:* CPO. *Pain:* their systems record; they don't
   decide. *Entry:* OEM/embed the decision engine (the engine is API-first and domain-agnostic).
4. **Enterprises with existing digital-twin programs** (manufacturing, airports, data centers).
   *Buyer:* Head of Digital / Industry 4.0. *Entry:* the agentic decision loop on top of the twin
   they already have — NEXUS's engine never imported the warehouse.

### Named targets — India first

| Category | Companies |
|---|---|
| 3PL / fulfillment networks | Delhivery, XpressBees, Ecom Express, Shadowfax, Emiza, Holisol, Stellar Value Chain, Mahindra Logistics, TVS Supply Chain |
| E-commerce in-house ops | Flipkart (eKart), Meesho (Valmo), Nykaa fulfillment, BigBasket, Zepto & Blinkit dark-store networks |
| Warehouse robotics (natural partners *and* acquirers) | GreyOrange, Addverb, Unbox Robotics, Ati Motors, Anscer Robotics, Peer Robotics |
| SC software | Increff, Unicommerce, Vinculum, FarEye, Locus.sh |

### Named targets — global

| Category | Companies |
|---|---|
| Robotics / AMR | Locus Robotics, Exotec, AutoStore (and their integrator networks), Geek+, Hai Robotics |
| 3PL / operators | GXO, DHL Supply Chain, Maersk (warehousing), Ryder, Lineage |
| SC software & twins | Körber, Blue Yonder, Manhattan Associates, o9 Solutions, Cosmo Tech, Siemens (Plant Simulation), Dassault (DELMIA), PTC, NVIDIA Omniverse ecosystem, AWS IoT TwinMaker / Azure Digital Twins partners |

*(Job-hunting? The same list is your target-employer list — plus AI-infra teams at NVIDIA, AWS,
Microsoft working on physical AI / simulation. This repo is the portfolio piece; lead with the
benchmark table and the safety architecture.)*

### Commercial motion (when asked "how do you make money?")

> "Land with a **paid pilot** — eight to twelve weeks, one site, ₹12–20L / $15–25k: we replay their
> historical order and event data through the twin and quantify the SLA and throughput gap between
> their current dispatching and the optimized loop. Convert to **per-site subscription**
> ($3–8k/month) for the live control room, what-if lab and decision engine; autopilot execution and
> new domain packs (factory, airport, dark store) are expansion. The wedge is simulation —
> nobody has to trust us with their operations on day one; they only have to look at the numbers."

---

## 7. Objections you will get — and the answers

**"It's a simulation. Reality is messier."**
> "Correct — that's why the architecture is event-sourced. The twin consumes the same typed events a
> real site emits from its WMS or fleet manager; the layout is parametric; drift is measurable
> because we track simulated-vs-realized KPIs per executed plan. And the first product motion —
> capacity and what-if studies — is valuable even at approximate fidelity. Fidelity is a dial, not
> a leap of faith."

**"Why won't the WMS vendors just build this?"**
> "They're systems of record with twenty-year-old schedulers; simulation-first decisioning is a
> different engine and a different culture — deterministic replay, forked worlds, risk gates. That's
> also why they're on my partner list: embedding NEXUS is faster than rebuilding it."

**"Isn't this just an LLM wrapper?"**
> "The LLM is optional and off by default in the cloud demo. It proposes; mathematics disposes —
> constraint validation, CP-SAT optimization, forked-world simulation, and a risk agent stand
> between any model output and the real world. Turn the LLM off and the benchmark barely moves."

**"NVIDIA Omniverse / Siemens already do digital twins."**
> "They do physics-grade *modeling* — CAD-heavy, weeks of setup, engineering audiences. NEXUS is
> decision-grade *operations*: minutes to a running twin, KPIs and plans, a shift manager as the
> user. Complementary, honestly — their twin could be my world model."

**"How do you integrate with a real warehouse?"**
> "An adapter maps their event stream — WMS webhooks, Kafka, RabbitMQ — into NEXUS's typed event
> vocabulary; the reducer does the rest. Execution runs the same path in reverse: approved plan →
> work orders via their API. The engine itself never changes; that's the point of the event boundary."

**"What's defensible here?"**
> "The compound loop: deterministic twin + cheap forking + solver + risk gating + replayable audit
> trail. Any one piece is buildable; the discipline of the whole — 96 tests, bit-identical replay in
> CI, benchmarked claims — is the moat a demo can't fake."

---

## 8. Closing lines (pick one)

- **Investor:** "The industry agrees physical AI is next; what's missing is the layer that decides.
  I've built it and benchmarked it solo in a form a team can extend to any operation with a floor
  plan. Let's talk about what two lighthouse customers and eighteen months look like."
- **Customer:** "Give me one site's historical event data and ninety days. If I can't show you a
  double-digit SLA improvement in simulation — your data, your layout, reproducible runs — you've
  lost nothing. If I can, you'll want it live."
- **Hiring manager:** "Everything you just saw — the twin, the solver, the agents, the UI, the CI,
  the benchmark — is one person applying production discipline to a hard systems problem. That's
  what I'd bring to your team."
