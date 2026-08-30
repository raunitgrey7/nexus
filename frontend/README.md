# NEXUS — Twin UI

Control-room frontend for the NEXUS digital twin: a live 3D/2D view of the warehouse, KPI bar, event feed,
decision pipeline (plan → simulate → risk → approve), what-if lab, forecasting, natural-language console,
timeline playback and benchmark charts.

Stack: **Next.js 15** (App Router, `src/`, TypeScript strict) · **Tailwind CSS v4** · **@react-three/fiber +
drei** (3D twin) · **recharts** (charts) · **zustand** (state) · **lucide-react** (icons).

## Run

```bash
npm install
cp .env.example .env.local          # edit if the backend is not on localhost:8000
npm run dev                          # http://localhost:3000
```

Fully offline demo (no backend), with synthetic robots, KPIs, events, decisions, what-ifs and forecasts:

```bash
NEXT_PUBLIC_MOCK=1 npm run dev       # PowerShell: $env:NEXT_PUBLIC_MOCK="1"; npm run dev
```

Quality gates: `npm run lint` · `npm run typecheck` · `npm run build` (`npm run format` runs Prettier).

Docker (multi-stage, node:24-alpine, non-root, port 3000, `output: "standalone"`):

```bash
docker build -t nexus-frontend \
  --build-arg NEXT_PUBLIC_API_URL=http://localhost:8000 \
  --build-arg NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws/live .
docker run -p 3000:3000 nexus-frontend
```

`docker compose up` at the repo root wires the same build args.

## Environment

| Variable              | Default                        | Purpose                                                    |
|-----------------------|--------------------------------|------------------------------------------------------------|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000`        | REST base URL (`/api/...`)                                 |
| `NEXT_PUBLIC_WS_URL`  | `ws://localhost:8000/ws/live`  | live WebSocket stream                                      |
| `NEXT_PUBLIC_MOCK`    | `0`                            | `1` = run against the in-browser mock (no backend needed)  |

All three are public and inlined at build time (pass them as Docker build args).

## Pages

| Route         | What it shows                                                                                                                                                                                                       |
|---------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `/`           | **Live Twin** — full-bleed 3D view (instanced shelves, congestion-tinted zones, docks/chargers, blocked cells, hatched closed zones, robots coloured by status with id labels and remaining path, smooth interpolation), 2D canvas toggle, KPI bar, sim controls (play/pause/step/speed/reset/autopilot; `space` = play/pause), right panel (events / robots / orders / zones), fault-injection bar and **Decide now** (opens the decision drawer). |
| `/decisions`  | Decision history + detail: situation, baseline vs candidates table (recommended highlighted), actions with rationale, risk findings, approval panel (approve / reject / execute), baseline-vs-plan timeline chart, timings, LLM badge. |
| `/whatif`     | What-If Lab: preset cards, custom scenario builder (mutation vocabulary with parameter forms, horizon, strategies, seeds), async run with polling, comparison table, KPI bar charts, timeline chart, best-strategy badge, narrative, history. |
| `/forecast`   | Demand chart with prediction band, trend badge, capacity vs forecast rate, projected-utilization gauge, battery table, congestion bars (now vs projected with capacity marker), bottleneck list.                          |
| `/console`    | Natural-language console: chat with suggestion chips, intent chip, LLM/deterministic badge, latency, inline what-if result card.                                                                                       |
| `/timeline`   | KPI history (breach, congestion, utilization, open orders) with notable-event markers, snapshot scrubber + playback rendered in the 2D view, notable-event list.                                                        |
| `/benchmarks` | Grouped bar charts per KPI per scale, normalised radar chart, results table and the runner's summary table (rendered defensively from whatever keys exist).                                                           |

## Architecture

```
src/
  app/                      App Router pages (all client components; data is fetched in effects)
    layout.tsx              fonts (Geist / Geist Mono, bundled locally) + AppShell
    page.tsx                Live Twin
    decisions/ whatif/ forecast/ console/ timeline/ benchmarks/
  lib/
    types.ts                TypeScript mirrors of backend/nexus/api/schemas.py + the world snapshot + WS frames
    api.ts                  NexusApi interface + typed fetch client for every endpoint in docs/API.md (ApiError)
    ws.ts                   LiveClient interface + WebSocket client (exponential reconnect, ping, frame validation)
    client.ts               picks REST/WS or the mock at runtime (mock is lazy-loaded, never in prod bundles)
    env.ts · format.ts · colors.ts · grid.ts (zone index, walkability, BFS)
  store/
    twinStore.ts            world snapshot, live robots merged from tick frames, KPIs, status, 300-event ring,
                            zone occupancy, dock/charger state, fault presets, sim control, selection, view mode
    decisionStore.ts        decisions list/detail, create + approve/reject/execute, drawer state
    whatifStore.ts          presets, history, run + poll until done (or WS `whatif` frame)
    forecastStore.ts        forecast + horizon (updated by WS `forecast` frames)
    consoleStore.ts         NLQ chat transcript
  components/
    shell/                  AppShell (boots the store), Rail (icon nav), TopBar (wordmark, sim clock, connection, chips)
    twin/                   TwinView3D (R3F), TwinView2D (canvas), KpiBar, SimControls, EventFeed, RobotsTable,
                            OrdersTable, ZonesTable, FaultBar, RightPanel, Legend
    decisions/              DecisionDetail, CandidatesTable, TimelineCompareChart, DecisionDrawer
    whatif/                 ScenarioBuilder, WhatIfResults, mutation vocabulary
    ui/                     Panel, Badge, Bar, Button, Select/Input/Toggle, Table/Tabs, Empty/Skeleton/Error
  mock/
    world.small.json        real fixture generated by the backend layout generator (`build_world(spec_for("small"))`)
    sim.ts                  in-browser engine: robots pick/deliver/charge on BFS paths, orders, KPIs, faults, snapshots
    fixtures.ts             fault & what-if presets, strategies, demo decision (R07 story), what-if/forecast/NLQ/benchmark synthesis
    api.ts · live.ts        NexusApi + LiveClient implementations over the sim
```

Data flow: `AppShell` calls `twinStore.boot()` once → `GET /api/world`, `/api/faults/presets`, `/api/strategies`,
`/api/events/recent`, then opens the live stream. `hello` replaces the world; `tick` frames merge robot
positions/status/battery/path, KPIs, zone occupancy, dock queues and charger occupants (the 3D/2D views lerp
toward the new cells every frame); `event` frames feed the ring buffer (structural events such as
`ZONE_CLOSED` or `AISLE_BLOCKED` trigger a world refresh); `decision`/`forecast`/`whatif`/`status` frames are
routed to their stores. If a tick frame also carries `blocked` / `closed_zones` (the live backend does, beyond
what API.md lists) they are merged into the grid immediately. Orders are not in tick frames, so the Orders tab
re-fetches `GET /api/world?grid=false` every 4 s while open. `GET /api/benchmarks` answering 404 (no results
file yet) renders as an empty state rather than an error.

## Mock mode

`NEXT_PUBLIC_MOCK=1` swaps `lib/client.ts` to `mock/api.ts` + `mock/live.ts`. The `MockSim` singleton animates
the small world: 12 robots take pending orders (priority-first for `optimized`/`nexus_full`, FIFO for
`baseline`), walk BFS paths to shelf access cells, pick, deliver to the least-loaded open dock, unload, and go
charging below 20 %. Orders arrive at the fixture's hourly demand curve; KPIs, a 60-tick KPI timeline and
600-tick snapshots are computed from the run. Fault presets mutate the sim (R07 failure, aisle blockage,
dock/zone closure, demand +40 %, charger disabled, worker delay). "Decide now" returns the demo decision
(trigger `ROBOT_FAILURE R07`, 46 candidates, recommended *Reassign R03 & R09 to zones B/C, prioritize
high-priority orders, reroute 14 tasks through corridor C4*, SLA breach 17.0 % → 4.2 %, risk LOW,
auto-approved); with autopilot on, failing R07 triggers it automatically. What-ifs resolve asynchronously
(queued → running → done) with strategy-dependent outcomes; the console answers by intent from live sim state.

Reset accepts scale/seed/strategy but the mock always animates the small fixture (the labels change).
