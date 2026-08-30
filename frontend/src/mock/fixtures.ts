/** Static fixtures + synthesisers for the offline mock (decisions, what-if, forecast, benchmarks, NLQ). */
import type {
  BatteryForecast,
  BenchmarkResults,
  Bottleneck,
  CongestionForecast,
  DecisionModel,
  FaultPreset,
  Forecast,
  KPIModel,
  MutationModel,
  NLQIntent,
  NLQResponse,
  PlanModel,
  RobotModel,
  SimulationOutcome,
  StrategyInfo,
  TimelinePoint,
  WhatIfPreset,
  WhatIfRequest,
  WhatIfResult,
  WhatIfRun,
  ZoneModel,
} from "@/lib/types";
import { mulberry32, simTimeIso } from "./sim";

export const STRATEGIES: StrategyInfo[] = [
  { name: "baseline", description: "FIFO orders, nearest-idle-robot assignment, shortest path." },
  { name: "optimized", description: "CP-SAT assignment (multi-objective), congestion-aware routing, EDF sequencing." },
  {
    name: "ai_planner",
    description: "Planner agent (LLM or deterministic fallback) chooses strategy parameters + reprioritisation.",
  },
  {
    name: "nexus_full",
    description: "Planner → Optimizer → simulate K candidate plans in forked worlds → Risk → pick best → Execute.",
  },
];

export const FAULT_PRESETS: FaultPreset[] = [
  {
    id: "fail_r07",
    name: "Fail R07",
    description: "Motor fault on robot R07 while it is carrying an order (45 min recovery).",
    event: { type: "ROBOT_FAILURE", entity_id: "R07", payload: { cause: "motor_fault", recovery_min: 45 } },
  },
  {
    id: "block_aisle_c",
    name: "Block aisle",
    description: "A spill blocks an aisle in Zone C; robots must reroute.",
    event: { type: "AISLE_BLOCKED", entity_id: "C", payload: { zone_id: "C", aisles: 1 } },
  },
  {
    id: "close_dock_d2",
    name: "Close dock D2",
    description: "Loading dock D2 goes offline; deliveries are diverted.",
    event: { type: "DOCK_CLOSED", entity_id: "D2", payload: {} },
  },
  {
    id: "demand_plus_40",
    name: "Demand +40%",
    description: "Order arrival rate rises by 40% for the rest of the shift.",
    event: { type: "DEMAND_CHANGED", entity_id: null, payload: { multiplier: 1.4 } },
  },
  {
    id: "close_zone_b",
    name: "Close Zone B",
    description: "Zone B becomes inaccessible (safety inspection).",
    event: { type: "ZONE_CLOSED", entity_id: "B", payload: {} },
  },
  {
    id: "disable_ch02",
    name: "Disable charger CH02",
    description: "Charging station CH02 is taken out of service.",
    event: { type: "CHARGER_DISABLED", entity_id: "CH02", payload: {} },
  },
  {
    id: "worker_w03_delay",
    name: "Delay worker W03",
    description: "Loader W03 is delayed by 30 minutes.",
    event: { type: "WORKER_DELAY", entity_id: "W03", payload: { minutes: 30 } },
  },
  {
    id: "clear_faults",
    name: "Clear aisles",
    description: "Remove all aisle blockages.",
    event: { type: "AISLE_CLEARED", entity_id: null, payload: {} },
  },
];

export const WHATIF_PRESETS: WhatIfPreset[] = [
  {
    id: "demand_rise_40",
    name: "Demand +40%",
    question: "What if demand rises 40%?",
    description: "Order arrivals increase by 40% for the whole horizon.",
    scenario: {
      name: "Demand +40%",
      description: "Multiply the arrival rate by 1.4",
      mutations: [{ type: "DEMAND_MULTIPLIER", params: { multiplier: 1.4 }, at_min: 0 }],
    },
  },
  {
    id: "remove_2_robots",
    name: "Remove 2 robots",
    question: "What if we remove 2 robots?",
    description: "Two robots are taken out of the fleet at the start of the horizon.",
    scenario: {
      name: "Fleet -2",
      description: "Remove two robots",
      mutations: [{ type: "REMOVE_ROBOTS", params: { count: 2 }, at_min: 0 }],
    },
  },
  {
    id: "zone_b_closed",
    name: "Zone B inaccessible",
    question: "What if Zone B is inaccessible?",
    description: "Zone B is closed for the whole horizon; its SKUs must be served from copies elsewhere.",
    scenario: {
      name: "Zone B closed",
      description: "Close zone B",
      mutations: [{ type: "CLOSE_ZONE", params: { zone_id: "B" }, at_min: 0 }],
    },
  },
  {
    id: "dock_d2_closed",
    name: "Dock D2 closed",
    question: "What if dock D2 closes for an hour?",
    description: "Dock D2 is unavailable from minute 5.",
    scenario: {
      name: "Dock D2 closed",
      description: "Close dock D2",
      mutations: [{ type: "CLOSE_DOCK", params: { dock_id: "D2" }, at_min: 5 }],
    },
  },
  {
    id: "burst_2x",
    name: "Demand burst 2×",
    question: "What if a 2× order burst hits for 30 minutes?",
    description: "A short, intense burst of orders.",
    scenario: {
      name: "Burst 2× / 30 min",
      description: "Demand burst",
      mutations: [{ type: "DEMAND_BURST", params: { multiplier: 2.0, duration_min: 30 }, at_min: 0 }],
    },
  },
  {
    id: "chargers_minus_2",
    name: "2 chargers offline",
    question: "What if two chargers go offline?",
    description: "Two charging stations are disabled.",
    scenario: {
      name: "Chargers -2",
      description: "Disable two chargers",
      mutations: [{ type: "DISABLE_CHARGERS", params: { count: 2 }, at_min: 0 }],
    },
  },
  {
    id: "batching_3",
    name: "Batch 3 orders per trip",
    question: "What if robots batch 3 orders per trip?",
    description: "Enable multi-order trips.",
    scenario: {
      name: "Batching ×3",
      description: "orders_per_trip=3",
      mutations: [{ type: "SET_BATCHING", params: { orders_per_trip: 3 }, at_min: 0 }],
    },
  },
];

// ------------------------------------------------------------------------------------------------
// helpers
// ------------------------------------------------------------------------------------------------

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}

function round(v: number, d = 3): number {
  const m = 10 ** d;
  return Math.round(v * m) / m;
}

function kpisFrom(base: KPIModel, over: Partial<KPIModel>, horizonTicks: number): KPIModel {
  const hours = horizonTicks / 3600;
  const throughput = over.throughput_per_hour ?? base.throughput_per_hour;
  const delivered = Math.round(throughput * hours);
  const breach = over.sla_breach_rate ?? base.sla_breach_rate;
  return {
    ...base,
    ...over,
    tick: base.tick + horizonTicks,
    sim_hours: round(base.sim_hours + hours),
    orders_delivered: base.orders_delivered + delivered,
    orders_late: base.orders_late + Math.round(delivered * breach),
    orders_created: base.orders_created + Math.round(delivered * 1.02),
  };
}

function timelineFor(
  base: KPIModel,
  startBreach: number,
  endBreach: number,
  startOpen: number,
  endOpen: number,
  horizonTicks: number,
  seed: number,
  congestion: number,
  utilization: number,
): TimelinePoint[] {
  const rng = mulberry32(seed);
  const pts: TimelinePoint[] = [];
  const n = 30;
  for (let i = 0; i <= n; i++) {
    const t = i / n;
    const ease = t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2;
    const noise = (rng() - 0.5) * 0.01;
    pts.push({
      tick: base.tick + Math.round(t * horizonTicks),
      open: Math.max(0, Math.round(startOpen + (endOpen - startOpen) * ease + (rng() - 0.5) * 4)),
      delivered: base.orders_delivered + Math.round(t * (base.throughput_per_hour || 300) * (horizonTicks / 3600)),
      breach_projected: round(clamp(startBreach + (endBreach - startBreach) * ease + noise, 0, 1), 4),
      congestion: round(Math.max(0, congestion * (0.6 + 0.6 * ease) + (rng() - 0.5) * 0.05)),
      utilization: round(clamp(utilization + (rng() - 0.5) * 0.04, 0, 1)),
    });
  }
  return pts;
}

// ------------------------------------------------------------------------------------------------
// decision (the demo story)
// ------------------------------------------------------------------------------------------------

export function makeDecision(id: string, tick: number, base: KPIModel, trigger = "ROBOT_FAILURE R07"): DecisionModel {
  const horizon = 5400;
  const baselineBreach = 0.17;
  const startBreach = Math.max(0.05, base.sla_breach_rate_projected);
  const openNow = Math.max(20, base.orders_open);
  const baselineKpis = kpisFrom(
    base,
    {
      sla_breach_rate: baselineBreach,
      sla_breach_rate_projected: baselineBreach,
      avg_fulfillment_min: round(Math.max(base.avg_fulfillment_min, 4.1) * 1.55, 2),
      p95_fulfillment_min: round(Math.max(base.p95_fulfillment_min, 7.5) * 1.6, 2),
      throughput_per_hour: round(Math.max(base.throughput_per_hour, 280) * 0.86, 1),
      robot_utilization: 0.91,
      congestion_index: round(Math.max(base.congestion_index, 0.05) + 0.42),
      robots_operational: base.robots_total - 1,
      failures: base.failures + 1,
    },
    horizon,
  );
  const baseline: SimulationOutcome = {
    horizon_ticks: horizon,
    kpis: baselineKpis,
    delta_vs_baseline: {},
    score: 1.0,
    timeline: timelineFor(base, startBreach, baselineBreach, openNow, openNow + 34, horizon, 11, 0.47, 0.91),
    duration_ms: 412,
  };

  const mk = (
    pid: string,
    name: string,
    source: PlanModel["source"],
    description: string,
    actions: PlanModel["actions"],
    breach: number,
    fulfil: number,
    throughput: number,
    congestion: number,
    risk: PlanModel["risk"],
    rank: number,
    seed: number,
    optimized = true,
  ): PlanModel => {
    const kpis = kpisFrom(
      base,
      {
        sla_breach_rate: breach,
        sla_breach_rate_projected: breach,
        avg_fulfillment_min: fulfil,
        p95_fulfillment_min: round(fulfil * 1.9, 2),
        throughput_per_hour: throughput,
        robot_utilization: round(clamp(0.72 + (0.2 - breach), 0.5, 0.95)),
        congestion_index: congestion,
        robots_operational: base.robots_total - 1,
        replans: base.replans + actions.length * 3,
      },
      horizon,
    );
    return {
      id: pid,
      name,
      source,
      description,
      actions,
      optimized,
      feasible: true,
      validation_errors: [],
      simulation: {
        horizon_ticks: horizon,
        kpis,
        delta_vs_baseline: {
          sla_breach_rate_projected: round(breach - baselineBreach, 4),
          avg_fulfillment_min: round(fulfil - baselineKpis.avg_fulfillment_min, 2),
          throughput_per_hour: round(throughput - baselineKpis.throughput_per_hour, 1),
          congestion_index: round(congestion - baselineKpis.congestion_index),
        },
        score: round(breach * 4 + fulfil / 10 + congestion * 0.5, 3),
        timeline: timelineFor(base, startBreach, breach, openNow, Math.round(openNow * (0.5 + breach * 3)), horizon, seed, congestion, 0.8),
        duration_ms: 380 + seed * 17,
      },
      risk,
      rank,
    };
  };

  const recommended = mk(
    "PLAN-A3F1",
    "Reassign R03 & R09 to zones B/C, prioritize high-priority orders, reroute 14 tasks through corridor C4",
    "llm",
    "Absorb R07's workload with the two nearest idle robots, protect HIGH/CRITICAL deadlines and steer traffic away from the congested Zone C aisles via corridor C4.",
    [
      {
        type: "REASSIGN_TASKS",
        params: { from_robots: ["R07"], to_robots: ["R03", "R09"], zones: ["B", "C"], max_tasks: 14 },
        rationale: "R03 and R09 are idle within 12 cells of R07's remaining picks; reassigning keeps travel minimal.",
      },
      {
        type: "REPRIORITIZE_ORDERS",
        params: { priority_at_least: "HIGH", boost_minutes: 5 },
        rationale: "6 HIGH/CRITICAL orders would otherwise breach within 9 minutes.",
      },
      {
        type: "PREFER_CORRIDOR",
        params: { corridors: ["C4"], bonus: 0.4, duration_min: 30 },
        rationale: "Corridor C4 has spare capacity; Zone C's inner aisles are at 2.3× capacity.",
      },
    ],
    0.042,
    round(Math.max(base.avg_fulfillment_min, 4.1) * 0.95, 2),
    round(Math.max(base.throughput_per_hour, 280) * 1.04, 1),
    0.08,
    {
      level: "LOW",
      score: 0.12,
      findings: [
        {
          kind: "capacity",
          severity: "low",
          message: "Corridor C4 utilisation reaches 78% of soft capacity during the reroute window.",
          entity_ids: ["C4"],
        },
        {
          kind: "instability",
          severity: "info",
          message: "Outcome stable across 3 seeds (σ SLA breach = 0.4 pp).",
          entity_ids: [],
        },
      ],
      stability: { sla_breach_std: 0.004, throughput_std: 6.1, seeds_agree: 1.0 },
      checked_seeds: 3,
    },
    1,
    21,
  );

  const candidates: PlanModel[] = [
    recommended,
    mk(
      "PLAN-B2C0",
      "Reassign R07 tasks to nearest idle robots",
      "heuristic",
      "Deterministic fallback: hand R07's tasks to the nearest available robots without routing changes.",
      [
        {
          type: "REASSIGN_TASKS",
          params: { from_robots: ["R07"], to_robots: ["R03", "R05", "R09"], max_tasks: 14 },
          rationale: "Nearest-idle reassignment.",
        },
      ],
      0.089,
      round(Math.max(base.avg_fulfillment_min, 4.1) * 1.12, 2),
      round(Math.max(base.throughput_per_hour, 280) * 0.97, 1),
      0.31,
      {
        level: "MEDIUM",
        score: 0.38,
        findings: [
          { kind: "capacity", severity: "medium", message: "Zone C stays above capacity for 22 minutes.", entity_ids: ["C"] },
        ],
        stability: { sla_breach_std: 0.012 },
        checked_seeds: 3,
      },
      2,
      33,
    ),
    mk(
      "PLAN-7D19",
      "Reprioritize HIGH orders + enable batching ×2",
      "optimizer",
      "Keep assignments, tighten deadlines for HIGH orders and allow two orders per trip.",
      [
        { type: "REPRIORITIZE_ORDERS", params: { priority_at_least: "HIGH", boost_minutes: 4 }, rationale: "Protect SLA of premium orders." },
        { type: "SET_BATCHING", params: { orders_per_trip: 2 }, rationale: "Fewer dock trips per order." },
      ],
      0.101,
      round(Math.max(base.avg_fulfillment_min, 4.1) * 1.18, 2),
      round(Math.max(base.throughput_per_hour, 280) * 1.01, 1),
      0.36,
      {
        level: "MEDIUM",
        score: 0.41,
        findings: [
          { kind: "regression", severity: "medium", message: "LOW/NORMAL orders' p95 fulfilment worsens by 2.6 min.", entity_ids: [] },
        ],
        stability: { sla_breach_std: 0.02 },
        checked_seeds: 3,
      },
      3,
      44,
    ),
    mk(
      "PLAN-E5A8",
      "Reroute all traffic away from Zone C",
      "llm",
      "Apply a strong routing penalty on Zone C to dissolve congestion.",
      [{ type: "REROUTE_AVOID_ZONE", params: { zones: ["C"], penalty: 6.0, duration_min: 30 }, rationale: "Zone C is the congestion hotspot." }],
      0.128,
      round(Math.max(base.avg_fulfillment_min, 4.1) * 1.3, 2),
      round(Math.max(base.throughput_per_hour, 280) * 0.9, 1),
      0.12,
      {
        level: "HIGH",
        score: 0.66,
        findings: [
          {
            kind: "deadlock",
            severity: "high",
            message: "Orders requiring Zone C shelves starve: 9 orders unreachable within the horizon.",
            entity_ids: ["C"],
          },
        ],
        stability: { sla_breach_std: 0.03 },
        checked_seeds: 3,
      },
      4,
      55,
    ),
    mk(
      "PLAN-0C77",
      "Send R04 and R11 to charge now, reassign later",
      "heuristic",
      "Pre-emptive charging to avoid a second outage, then reassign.",
      [
        { type: "SEND_TO_CHARGE", params: { robot_ids: ["R04", "R11"], after_current_task: true }, rationale: "Both below 30% battery." },
        { type: "REASSIGN_TASKS", params: { from_robots: ["R07"], to_robots: ["R03"], max_tasks: 14 }, rationale: "Single-robot takeover." },
      ],
      0.151,
      round(Math.max(base.avg_fulfillment_min, 4.1) * 1.42, 2),
      round(Math.max(base.throughput_per_hour, 280) * 0.88, 1),
      0.29,
      {
        level: "MEDIUM",
        score: 0.44,
        findings: [
          { kind: "resource_exhaustion", severity: "medium", message: "Fleet capacity drops to 9 productive robots for 18 min.", entity_ids: ["R04", "R11"] },
        ],
        stability: { sla_breach_std: 0.018 },
        checked_seeds: 3,
      },
      5,
      66,
      false,
    ),
    mk(
      "PLAN-NOOP",
      "Do nothing (reference)",
      "heuristic",
      "Reference plan: no intervention.",
      [{ type: "NOOP", params: {}, rationale: "Reference." }],
      baselineBreach,
      baselineKpis.avg_fulfillment_min,
      baselineKpis.throughput_per_hour,
      baselineKpis.congestion_index,
      {
        level: "HIGH",
        score: 0.71,
        findings: [
          { kind: "regression", severity: "high", message: "SLA breach projected at 17.0% within 90 minutes.", entity_ids: [] },
        ],
        stability: { sla_breach_std: 0.011 },
        checked_seeds: 3,
      },
      6,
      77,
      false,
    ),
  ];

  return {
    id,
    created_tick: tick,
    sim_time: simTimeIso(tick),
    trigger,
    goal: "Minimize SLA breaches and fulfillment delay",
    status: "approved",
    situation: {
      trigger_event: "ROBOT_FAILURE",
      robot: "R07",
      cause: "motor_fault",
      tasks_affected: 14,
      orders_at_risk: 23,
      hot_zone: "C",
      zone_c_occupancy: "7 / 3",
      idle_robots: ["R03", "R09"],
      summary:
        "R07 failed (motor fault) mid-task in Zone C with 14 tasks queued. Zone C is at 2.3× capacity and 23 open orders — 6 of them HIGH/CRITICAL — would breach their SLA within the 90-minute horizon.",
    },
    baseline,
    candidates,
    recommended_plan_id: "PLAN-A3F1",
    approval: {
      policy: "auto",
      auto_approved: true,
      reason: "Risk LOW (0.12) and projected SLA-breach gain 12.8 pp exceeds the 2 pp auto-approval threshold.",
      approved_by: "ops-manager-agent",
      approved_tick: tick,
    },
    explanation:
      "R07 failed in Zone C while carrying an order, leaving 14 tasks unassigned in the hottest zone of the floor. 46 candidate strategies were generated (LLM planner + optimizer variants) and each was simulated for 90 minutes in a forked world. Reassigning R07's work to R03 and R09, boosting HIGH-priority orders and biasing routes through corridor C4 cuts the projected SLA breach from 17.0% to 4.2% while keeping congestion at 0.08. The plan passed all constraint checks and was stable across 3 seeds, so it was auto-approved.",
    timings: { planning_ms: 1840, validation_ms: 22, optimization_ms: 611, simulation_ms: 4270, risk_ms: 318, total_ms: 7061 },
    candidates_evaluated: 46,
    llm_used: true,
    llm_model: "qwen2.5:7b",
  };
}

// ------------------------------------------------------------------------------------------------
// what-if
// ------------------------------------------------------------------------------------------------

function scenarioSeverity(mutations: MutationModel[]): number {
  let s = 0;
  for (const m of mutations) {
    const p = m.params;
    const n = (k: string, d: number) => (typeof p[k] === "number" ? (p[k] as number) : d);
    const ids = (k: string) => (Array.isArray(p[k]) ? (p[k] as unknown[]).length : 0);
    switch (m.type) {
      case "DEMAND_MULTIPLIER":
        s += (n("multiplier", 1) - 1) * 0.5;
        break;
      case "DEMAND_BURST":
        s += (n("multiplier", 1) - 1) * 0.12 * Math.min(1, n("duration_min", 30) / 30);
        break;
      case "REMOVE_ROBOTS":
        s += Math.max(n("count", 0), ids("robot_ids")) * 0.06;
        break;
      case "ADD_ROBOTS":
        s -= n("count", 0) * 0.05;
        break;
      case "ROBOT_FAILURE":
        s += Math.max(1, ids("robot_ids")) * 0.05;
        break;
      case "CLOSE_ZONE":
        s += 0.12;
        break;
      case "CLOSE_DOCK":
        s += 0.08;
        break;
      case "DISABLE_CHARGERS":
        s += Math.max(n("count", 0), ids("charger_ids")) * 0.035;
        break;
      case "BLOCK_AISLE":
        s += 0.05;
        break;
      case "MOVE_INVENTORY":
        s -= 0.03;
        break;
      case "WORKER_DELAY":
        s += 0.04;
        break;
      case "SET_SLA":
        s += 0.06;
        break;
      case "SET_BATCHING":
        s -= 0.04;
        break;
    }
  }
  return s;
}

const MITIGATION: Record<string, number> = { baseline: 1.0, optimized: 0.62, ai_planner: 0.5, nexus_full: 0.3 };

export function makeWhatIfResult(id: string, req: WhatIfRequest, base: KPIModel, tick: number): WhatIfResult {
  const horizonTicks = req.horizon_min * 60;
  const severity = scenarioSeverity(req.scenario.mutations);
  const refBreach = clamp(Math.max(0.03, base.sla_breach_rate_projected), 0, 0.6);
  const refFulfil = Math.max(3.2, base.avg_fulfillment_min);
  const refThroughput = Math.max(260, base.throughput_per_hour);
  const refCongestion = Math.max(0.04, base.congestion_index);
  const openNow = Math.max(15, base.orders_open);

  const mkRun = (strategy: string, label: string, seed: number, factor: number, reference = false): WhatIfRun => {
    const rng = mulberry32(seed * 7 + strategy.length);
    const jitter = (rng() - 0.5) * 0.01;
    const breach = clamp(refBreach + severity * factor + jitter, 0.005, 0.95);
    const fulfil = round(refFulfil * (1 + severity * factor * 1.4) * (reference ? 1 : 1 - (1 - factor) * 0.08), 2);
    const throughput = round(refThroughput * (1 - Math.max(0, severity) * factor * 0.45 + Math.max(0, -severity) * 0.3) * (reference ? 1 : 1 + (1 - factor) * 0.05), 1);
    const congestion = round(refCongestion * (1 + severity * factor * 2.5), 3);
    const utilization = round(clamp(0.7 + Math.max(0, severity) * 0.3 - (1 - factor) * 0.05, 0.3, 0.97));
    const kpis = kpisFrom(
      base,
      {
        sla_breach_rate: round(breach, 4),
        sla_breach_rate_projected: round(breach, 4),
        avg_fulfillment_min: fulfil,
        p95_fulfillment_min: round(fulfil * 1.9, 2),
        throughput_per_hour: throughput,
        robot_utilization: utilization,
        congestion_index: congestion,
      },
      horizonTicks,
    );
    return {
      strategy,
      label,
      seed,
      kpis,
      delta_vs_reference: {},
      timeline: timelineFor(base, refBreach, breach, openNow, Math.round(openNow * (1 + severity * factor * 2)), horizonTicks, seed + strategy.length, congestion, utilization),
      duration_ms: 900 + Math.round(rng() * 1400),
    };
  };

  const reference = req.include_current ? mkRun("current", "Current world (no scenario)", 1, 0, true) : null;
  const runs: WhatIfRun[] = [];
  for (const strategy of req.strategies) {
    const factor = MITIGATION[strategy] ?? 0.8;
    for (let s = 1; s <= req.seeds; s++) runs.push(mkRun(strategy, `${strategy} · seed ${s}`, s, factor));
  }
  if (reference) {
    for (const r of runs) {
      r.delta_vs_reference = {
        sla_breach_rate_projected: round(r.kpis.sla_breach_rate_projected - reference.kpis.sla_breach_rate_projected, 4),
        avg_fulfillment_min: round(r.kpis.avg_fulfillment_min - reference.kpis.avg_fulfillment_min, 2),
        throughput_per_hour: round(r.kpis.throughput_per_hour - reference.kpis.throughput_per_hour, 1),
        congestion_index: round(r.kpis.congestion_index - reference.kpis.congestion_index, 3),
      };
    }
  }
  const byStrategy = new Map<string, WhatIfRun[]>();
  for (const r of runs) byStrategy.set(r.strategy, [...(byStrategy.get(r.strategy) ?? []), r]);
  const comparison = [...byStrategy.entries()].map(([strategy, rs]) => {
    const avg = (f: (k: KPIModel) => number) => round(rs.reduce((a, r) => a + f(r.kpis), 0) / rs.length, 4);
    return {
      strategy,
      runs: rs.length,
      sla_breach_rate_projected: avg((k) => k.sla_breach_rate_projected),
      avg_fulfillment_min: avg((k) => k.avg_fulfillment_min),
      throughput_per_hour: avg((k) => k.throughput_per_hour),
      congestion_index: avg((k) => k.congestion_index),
      robot_utilization: avg((k) => k.robot_utilization),
    };
  });
  const best = comparison.slice().sort((a, b) => a.sla_breach_rate_projected - b.sla_breach_rate_projected)[0];
  const worst = comparison.slice().sort((a, b) => b.sla_breach_rate_projected - a.sla_breach_rate_projected)[0];
  const narrative = best
    ? `Under "${req.scenario.name}", ${best.strategy} performs best with a projected SLA breach of ${(best.sla_breach_rate_projected * 100).toFixed(1)}% ` +
      `(${worst && worst.strategy !== best.strategy ? `vs ${(worst.sla_breach_rate_projected * 100).toFixed(1)}% for ${worst.strategy}` : "single strategy"}), ` +
      `average fulfilment ${best.avg_fulfillment_min.toFixed(1)} min and throughput ${best.throughput_per_hour.toFixed(0)} orders/h over a ${req.horizon_min}-minute horizon` +
      (reference ? ` — ${(Math.abs(best.sla_breach_rate_projected - reference.kpis.sla_breach_rate_projected) * 100).toFixed(1)} pp ${best.sla_breach_rate_projected >= reference.kpis.sla_breach_rate_projected ? "worse" : "better"} than the unmodified world.` : ".")
    : "No strategies were evaluated.";

  return {
    id,
    status: "done",
    scenario: req.scenario,
    created_tick: tick,
    horizon_ticks: horizonTicks,
    reference,
    runs,
    best_strategy: best?.strategy ?? null,
    narrative,
    comparison,
    error: null,
  };
}

// ------------------------------------------------------------------------------------------------
// forecast
// ------------------------------------------------------------------------------------------------

export function makeForecast(
  tick: number,
  base: KPIModel,
  robots: RobotModel[],
  zones: ZoneModel[],
  occupancy: Record<string, number>,
  demandMultiplier: number,
  horizonMin = 60,
): Forecast {
  const rng = mulberry32(tick + 7);
  const hour = new Date(Date.UTC(2026, 0, 5, 8, 0, 0) + tick * 1000).getUTCHours();
  const mult = [0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.1, 0.3, 0.8, 1.0, 1.15, 1.25, 1.2, 1.0, 1.1, 1.2, 1.0, 0.7, 0.3, 0.15, 0.1, 0.05, 0.05, 0.05];
  const baseRate = 340 * demandMultiplier;
  const current = baseRate * mult[hour % 24];
  const buckets = [];
  const bucketMin = 10;
  let total = 0;
  for (let i = 0; i < horizonMin / bucketMin; i++) {
    const h = (hour + Math.floor((i * bucketMin) / 60)) % 24;
    const expected = (baseRate * mult[h] * bucketMin) / 60;
    const spread = expected * (0.12 + i * 0.02);
    total += expected;
    buckets.push({
      start_min: i * bucketMin,
      end_min: (i + 1) * bucketMin,
      expected_orders: round(expected, 1),
      lower: round(Math.max(0, expected - spread), 1),
      upper: round(expected + spread, 1),
    });
  }
  const forecastRate = (total / horizonMin) * 60;
  const capacity = Math.max(300, base.robots_operational * 34);
  const trend = forecastRate > current * 1.05 ? "rising" : forecastRate < current * 0.95 ? "falling" : "flat";

  const battery: BatteryForecast[] = robots
    .map((r) => {
      const drainPerMin = r.status === "charging" ? -9 : r.status === "idle" || r.status === "failed" ? 0.05 : 0.9 + rng() * 0.5;
      const exhaustion = drainPerMin > 0.1 ? round(Math.max(0, (r.battery - 5) / drainPerMin), 1) : null;
      const risk: BatteryForecast["risk"] = exhaustion !== null && exhaustion < 20 ? "high" : exhaustion !== null && exhaustion < 45 ? "medium" : "low";
      const eta = r.status === "charging" ? 0 : round(3 + rng() * 6, 1);
      return {
        robot_id: r.id,
        battery: r.battery,
        status: r.status,
        workload_tasks: r.task_id ? 1 + Math.floor(rng() * 3) : 0,
        predicted_exhaustion_min: exhaustion,
        charger_eta_min: eta,
        risk,
        recommendation:
          r.status === "charging"
            ? "Charging — release at 90%."
            : risk === "high"
              ? `Send to charge after current task (exhaustion in ~${exhaustion} min).`
              : risk === "medium"
                ? "Schedule charging within the next 30 minutes."
                : "No action needed.",
      };
    })
    .sort((a, b) => a.battery - b.battery);

  const congestion: CongestionForecast[] = zones
    .filter((z) => z.kind === "storage" || z.kind === "corridor")
    .map((z) => {
      const now = occupancy[z.id] ?? 0;
      const projected = round(Math.max(0, now * (0.7 + rng() * 0.8) + (forecastRate / capacity) * 1.2), 1);
      const ratio = projected / Math.max(1, z.capacity);
      const risk: CongestionForecast["risk"] = ratio >= 1 ? "high" : ratio >= 0.7 ? "medium" : "low";
      const drivers: string[] = [];
      if (now >= z.capacity) drivers.push(`${now} robots inside now (capacity ${z.capacity})`);
      if (trend === "rising") drivers.push("rising demand");
      if (z.id === "C") drivers.push("hot SKUs concentrated in this zone");
      return {
        zone_id: z.id,
        zone_name: z.name,
        robots_now: now,
        capacity: z.capacity,
        projected_robots: projected,
        projected_change_pct: round(now ? ((projected - now) / now) * 100 : projected * 100, 1),
        eta_min: round(5 + rng() * 25, 1),
        risk,
        drivers,
      };
    })
    .sort((a, b) => b.projected_robots / b.capacity - a.projected_robots / a.capacity);

  const bottlenecks: Bottleneck[] = [];
  const hot = congestion[0];
  if (hot && hot.risk !== "low") {
    bottlenecks.push({
      kind: "zone",
      entity_id: hot.zone_id,
      severity: round(clamp(hot.projected_robots / hot.capacity / 2, 0, 1), 2),
      message: `${hot.zone_name} is projected to reach ${hot.projected_robots} robots (capacity ${hot.capacity}) in ~${hot.eta_min} min.`,
      recommendation: `Apply a routing penalty on ${hot.zone_id} or reposition its hottest SKUs to a neighbouring zone.`,
    });
  }
  const lowBat = battery.filter((b) => b.risk === "high");
  if (lowBat.length) {
    bottlenecks.push({
      kind: "robot",
      entity_id: lowBat[0].robot_id,
      severity: round(clamp(0.4 + lowBat.length * 0.15, 0, 1), 2),
      message: `${lowBat.length} robot(s) will exhaust their battery within 20 minutes (${lowBat.map((b) => b.robot_id).join(", ")}).`,
      recommendation: "Pre-emptively send them to charge after their current task.",
    });
  }
  if (forecastRate / capacity > 0.85) {
    bottlenecks.push({
      kind: "demand",
      entity_id: "demand",
      severity: round(clamp(forecastRate / capacity - 0.5, 0, 1), 2),
      message: `Forecast demand (${forecastRate.toFixed(0)}/h) reaches ${((forecastRate / capacity) * 100).toFixed(0)}% of fleet capacity (${capacity}/h).`,
      recommendation: "Enable batching (2–3 orders per trip) or add robots for the peak.",
    });
  }
  const failed = robots.filter((r) => r.status === "failed");
  if (failed.length) {
    bottlenecks.push({
      kind: "robot",
      entity_id: failed[0].id,
      severity: 0.7,
      message: `${failed.map((r) => r.id).join(", ")} failed; fleet capacity reduced by ${((failed.length / Math.max(1, robots.length)) * 100).toFixed(0)}%.`,
      recommendation: "Reassign their tasks and run the decision pipeline.",
    });
  }
  if (bottlenecks.length === 0) {
    bottlenecks.push({
      kind: "dock",
      entity_id: "D1",
      severity: 0.18,
      message: "Dock D1 handles 38% of deliveries; queue length peaks at 2.",
      recommendation: "Balance dock choice by queue length (already active in optimized/nexus_full).",
    });
  }

  return {
    generated_tick: tick,
    sim_time: simTimeIso(tick),
    demand: {
      horizon_min: horizonMin,
      expected_orders: round(total, 1),
      per_bucket: buckets,
      trend,
      current_rate_per_hour: round(current, 1),
      forecast_rate_per_hour: round(forecastRate, 1),
      capacity_per_hour: capacity,
      projected_utilization: round(forecastRate / capacity),
      confidence: 0.82,
      method: "holt_winters",
    },
    battery,
    congestion,
    bottlenecks,
    summary: `Demand ${trend} (${forecastRate.toFixed(0)}/h vs ${current.toFixed(0)}/h now, ${((forecastRate / capacity) * 100).toFixed(0)}% of capacity). ${bottlenecks.length} bottleneck(s) detected; ${lowBat.length} robot(s) at battery risk.`,
  };
}

// ------------------------------------------------------------------------------------------------
// benchmarks
// ------------------------------------------------------------------------------------------------

export const BENCHMARKS: BenchmarkResults = (() => {
  const strategies = ["baseline", "optimized", "ai_planner", "nexus_full"];
  const scaleBase: Record<string, { breach: number; fulfil: number; thr: number; util: number; cong: number; dist: number; energy: number }> = {
    small: { breach: 0.146, fulfil: 6.8, thr: 318, util: 0.71, cong: 0.42, dist: 61_000, energy: 1_240 },
    medium: { breach: 0.191, fulfil: 8.1, thr: 842, util: 0.76, cong: 0.98, dist: 210_000, energy: 4_300 },
    large: { breach: 0.238, fulfil: 9.6, thr: 1_480, util: 0.79, cong: 1.85, dist: 540_000, energy: 11_200 },
  };
  const factor: Record<string, number> = { baseline: 1, optimized: 0.55, ai_planner: 0.47, nexus_full: 0.27 };
  const scales: BenchmarkResults["scales"] = {};
  const summary_table: Array<Record<string, unknown>> = [];
  for (const [scale, b] of Object.entries(scaleBase)) {
    const entry: Record<string, { kpis_mean: Record<string, number>; kpis_std: Record<string, number>; runs: number }> = {};
    for (const s of strategies) {
      const f = factor[s];
      const mean = {
        sla_breach_rate: round(b.breach * f, 4),
        avg_fulfillment_min: round(b.fulfil * (0.62 + 0.38 * f), 2),
        p95_fulfillment_min: round(b.fulfil * (0.62 + 0.38 * f) * 2.1, 2),
        throughput_per_hour: round(b.thr * (1.14 - 0.14 * f), 1),
        robot_utilization: round(b.util * (1.1 - 0.1 * f), 3),
        congestion_index: round(b.cong * f, 3),
        distance_total: Math.round(b.dist * (0.86 + 0.14 * f)),
        energy_total: round(b.energy * (0.86 + 0.14 * f), 1),
        planning_latency_s: round(s === "nexus_full" ? 6.8 : s === "ai_planner" ? 2.4 : s === "optimized" ? 0.9 : 0.1, 2),
      };
      entry[s] = {
        kpis_mean: mean,
        kpis_std: {
          sla_breach_rate: round(mean.sla_breach_rate * 0.08, 4),
          avg_fulfillment_min: round(mean.avg_fulfillment_min * 0.05, 2),
          throughput_per_hour: round(mean.throughput_per_hour * 0.03, 1),
          robot_utilization: 0.012,
          congestion_index: round(mean.congestion_index * 0.1, 3),
        },
        runs: 3,
      };
      summary_table.push({ scale, strategy: s, runs: 3, ...mean });
    }
    scales[scale] = { strategies: entry };
  }
  return { generated_at: "2026-08-28T14:12:00+00:00", scales, summary_table };
})();

// ------------------------------------------------------------------------------------------------
// NLQ
// ------------------------------------------------------------------------------------------------

export const NLQ_SUGGESTIONS = [
  "Why are orders slowing down?",
  "What happens if order volume increases 40%?",
  "Which robot should charge next?",
  "What is the current status?",
  "What does the demand forecast look like?",
  "Tell me about robot R07",
];

export function answerNlq(
  question: string,
  ctx: {
    kpis: KPIModel;
    robots: RobotModel[];
    zones: ZoneModel[];
    occupancy: Record<string, number>;
    recentNotable: string[];
    forecast: Forecast;
    runWhatIf: (req: WhatIfRequest) => WhatIfResult;
  },
): NLQResponse {
  const q = question.toLowerCase();
  const k = ctx.kpis;
  const latency = 140 + Math.round(Math.random() * 600);
  const base = { llm_used: false, model: null, latency_ms: latency, suggestions: NLQ_SUGGESTIONS.slice(0, 3) };
  let intent: NLQIntent = "unknown";

  const pctMatch = q.match(/(\d{1,3})\s*%/);
  if (q.includes("what if") || q.includes("what happens if") || q.includes("increase") || q.includes("remove")) {
    intent = "whatif";
    const mult = pctMatch ? 1 + Number(pctMatch[1]) / 100 : 1.4;
    const removeMatch = q.match(/remove\s+(\d+)\s+robot/);
    const mutations: MutationModel[] = removeMatch
      ? [{ type: "REMOVE_ROBOTS", params: { count: Number(removeMatch[1]) }, at_min: 0 }]
      : [{ type: "DEMAND_MULTIPLIER", params: { multiplier: round(mult, 2) }, at_min: 0 }];
    const name = removeMatch ? `Remove ${removeMatch[1]} robots` : `Demand ×${round(mult, 2)}`;
    const result = ctx.runWhatIf({
      scenario: { name, description: question, mutations },
      strategies: ["baseline", "optimized", "nexus_full"],
      horizon_min: 60,
      seeds: 1,
      include_current: true,
    });
    return {
      ...base,
      intent,
      answer: `I simulated "${name}" for 60 minutes across three strategies.\n${result.narrative}\nRecommendation: keep ${result.best_strategy} active and watch Zone C congestion.`,
      data: { whatif: result },
      suggestions: ["Which robot should charge next?", "Why are orders slowing down?", "Run the same scenario for 2 hours"],
    };
  }
  if (q.startsWith("why") || q.includes("slow") || q.includes("late") || q.includes("delay")) {
    intent = "explain";
    const hot = ctx.zones
      .filter((z) => z.kind === "storage")
      .map((z) => ({ z, r: (ctx.occupancy[z.id] ?? 0) / Math.max(1, z.capacity) }))
      .sort((a, b) => b.r - a.r)[0];
    const failed = ctx.robots.filter((r) => r.status === "failed").map((r) => r.id);
    const lines = [
      `Projected SLA breach is ${(k.sla_breach_rate_projected * 100).toFixed(1)}% with ${k.orders_open} open orders (${k.orders_overdue_open} already overdue).`,
      failed.length ? `${failed.join(", ")} ${failed.length > 1 ? "are" : "is"} failed, reducing fleet capacity to ${k.robots_operational}/${k.robots_total} robots.` : `All ${k.robots_total} robots are operational.`,
      hot ? `${hot.z.name} holds ${ctx.occupancy[hot.z.id] ?? 0} robots against a capacity of ${hot.z.capacity} (${(hot.r * 100).toFixed(0)}%), which slows picks there.` : "",
      ctx.recentNotable.length ? `Recent notable events: ${ctx.recentNotable.slice(-3).join(" · ")}.` : "",
      `Average fulfilment is ${k.avg_fulfillment_min.toFixed(1)} min (p95 ${k.p95_fulfillment_min.toFixed(1)} min); throughput ${k.throughput_per_hour.toFixed(0)}/h.`,
    ].filter(Boolean);
    return { ...base, intent, answer: lines.join("\n"), data: { kpis: k, failed_robots: failed, hot_zone: hot?.z.id ?? null } };
  }
  if (q.includes("charge") || q.includes("battery")) {
    intent = "recommend";
    const candidates = ctx.forecast.battery.filter((b) => b.status !== "charging" && b.status !== "failed").slice(0, 3);
    const first = candidates[0];
    return {
      ...base,
      intent,
      answer: first
        ? `${first.robot_id} should charge next: battery ${first.battery.toFixed(0)}%, predicted exhaustion in ${first.predicted_exhaustion_min ?? "—"} min, charger ETA ${first.charger_eta_min ?? "—"} min.\nNext in line: ${candidates.slice(1).map((b) => `${b.robot_id} (${b.battery.toFixed(0)}%)`).join(", ") || "none"}.`
        : "No robot needs charging right now.",
      data: { battery: candidates },
    };
  }
  if (q.includes("forecast") || q.includes("demand") || q.includes("next hour")) {
    intent = "forecast";
    const d = ctx.forecast.demand;
    return {
      ...base,
      intent,
      answer: `Demand is ${d.trend}: ${d.forecast_rate_per_hour.toFixed(0)} orders/h expected over the next ${d.horizon_min} min vs ${d.current_rate_per_hour.toFixed(0)}/h now.\nFleet capacity is ${d.capacity_per_hour}/h → projected utilisation ${(d.projected_utilization * 100).toFixed(0)}% (confidence ${(d.confidence * 100).toFixed(0)}%).\n${ctx.forecast.bottlenecks[0]?.message ?? ""}`,
      data: { forecast: ctx.forecast.demand, bottlenecks: ctx.forecast.bottlenecks },
    };
  }
  const entity = q.match(/\b(r\d{2}|ord-\d{6}|zone\s+[a-l]|d\d|ch\d{2}|w\d{2})\b/);
  if (entity) {
    intent = "entity";
    const id = entity[1].toUpperCase().replace("ZONE ", "");
    const robot = ctx.robots.find((r) => r.id === id);
    const zone = ctx.zones.find((z) => z.id === id);
    if (robot) {
      return {
        ...base,
        intent,
        answer: `${robot.id} is ${robot.status} in zone ${robot.zone_id} at cell [${robot.cell[0]}, ${robot.cell[1]}] with ${robot.battery.toFixed(0)}% battery${robot.task_id ? `, working on ${robot.task_id}` : ""}${robot.failure_cause ? ` (failure: ${robot.failure_cause})` : ""}.\nDistance travelled ${robot.distance} cells, ${robot.orders_completed} orders completed.`,
        data: { entity: robot },
      };
    }
    if (zone) {
      return {
        ...base,
        intent,
        answer: `${zone.name} (${zone.kind}) spans [${zone.x0},${zone.y0}]–[${zone.x1},${zone.y1}], capacity ${zone.capacity}, currently ${ctx.occupancy[zone.id] ?? 0} robots${zone.closed ? ", CLOSED" : ""}.`,
        data: { entity: zone },
      };
    }
    return { ...base, intent, answer: `I could not find an entity named ${id}.`, data: {} };
  }
  if (q.includes("status") || q.includes("how many") || q.includes("open orders") || q.includes("overview")) {
    intent = "status";
    return {
      ...base,
      intent,
      answer: `Tick ${k.tick} (${k.sim_hours.toFixed(2)} h): ${k.orders_open} open orders (${k.orders_pending} pending), ${k.orders_delivered} delivered, ${k.orders_late} late.\nProjected SLA breach ${(k.sla_breach_rate_projected * 100).toFixed(1)}%, throughput ${k.throughput_per_hour.toFixed(0)}/h, utilisation ${(k.robot_utilization * 100).toFixed(0)}%, congestion ${k.congestion_index.toFixed(2)}.\nRobots operational: ${k.robots_operational}/${k.robots_total}.`,
      data: { kpis: k },
    };
  }
  return {
    ...base,
    intent,
    answer:
      "I can explain slowdowns, run what-if scenarios, report status, forecast demand, recommend charging and describe entities (e.g. R07, Zone C). Try one of the suggestions.",
    data: {},
    suggestions: NLQ_SUGGESTIONS,
  };
}
