/** `NexusApi` implementation backed by the in-browser `MockSim`. */
import type { NexusApi } from "@/lib/api";
import { ApiError } from "@/lib/api";
import { NOTABLE_EVENT_TYPES } from "@/lib/colors";
import type {
  Cell,
  DecisionModel,
  EventModel,
  InjectEventRequest,
  SpatialResponse,
  WhatIfRequest,
  WhatIfResult,
} from "@/lib/types";
import {
  BENCHMARKS,
  FAULT_PRESETS,
  STRATEGIES,
  WHATIF_PRESETS,
  answerNlq,
  makeDecision,
  makeForecast,
  makeWhatIfResult,
} from "./fixtures";
import { getSim } from "./sim";

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

const decisions: DecisionModel[] = [];
const whatifs: WhatIfResult[] = [];
let decisionN = 0;
let whatifN = 0;
let decisionTimer: ReturnType<typeof setTimeout> | null = null;

function applyInjected(req: InjectEventRequest): EventModel {
  const sim = getSim();
  const p = req.payload ?? {};
  const num = (k: string, d: number) => (typeof p[k] === "number" ? (p[k] as number) : d);
  const str = (k: string, d: string) => (typeof p[k] === "string" ? (p[k] as string) : d);
  switch (req.type) {
    case "ROBOT_FAILURE": {
      const ev = sim.failRobot(req.entity_id ?? "R07", str("cause", "motor_fault"), num("recovery_min", 45));
      if (!ev) throw new ApiError(404, `unknown robot ${req.entity_id}`, "/api/events/inject");
      if (sim.autopilot) scheduleAutoDecision(`ROBOT_FAILURE ${req.entity_id ?? "R07"}`);
      return ev;
    }
    case "AISLE_BLOCKED":
      return sim.blockAisle(req.entity_id ?? str("zone_id", "C"), Array.isArray(p.cells) ? (p.cells as Cell[]) : undefined);
    case "AISLE_CLEARED":
      return sim.clearAisles();
    case "ZONE_CLOSED":
      return sim.closeZone(req.entity_id ?? str("zone_id", "B"));
    case "ZONE_OPENED":
      return sim.openZone(req.entity_id ?? str("zone_id", "B"));
    case "DOCK_CLOSED":
      return sim.closeDock(req.entity_id ?? str("dock_id", "D2"));
    case "DOCK_OPENED":
      return sim.openDock(req.entity_id ?? str("dock_id", "D2"));
    case "DEMAND_CHANGED":
      if (typeof p.burst_multiplier === "number") return sim.burst(p.burst_multiplier, num("duration_min", 30));
      return sim.changeDemand(num("multiplier", 1.4) * (num("relative", 0) ? sim.demandMultiplier : 1));
    case "CHARGER_DISABLED":
      return sim.disableCharger(req.entity_id ?? str("charger_id", "CH02"));
    case "WORKER_DELAY":
      return sim.delayWorker(req.entity_id ?? str("worker_id", "W03"), num("minutes", 30));
    default:
      return sim.pushEvent(req.type, req.entity_id ?? null, p, "user");
  }
}

function scheduleAutoDecision(trigger: string): void {
  if (decisionTimer) clearTimeout(decisionTimer);
  decisionTimer = setTimeout(() => {
    void createDecision(trigger, true);
  }, 2500);
}

async function createDecision(trigger: string, auto = false): Promise<DecisionModel> {
  const sim = getSim();
  decisionN++;
  const id = `DEC-${String(decisionN).padStart(4, "0")}`;
  if (!auto) await sleep(1600);
  const d = makeDecision(id, sim.tick, sim.kpis(), trigger);
  decisions.unshift(d);
  sim.planEvent("PLAN_PROPOSED", d.id, { candidates: d.candidates_evaluated, recommended: d.recommended_plan_id });
  sim.planEvent("PLAN_APPROVED", d.id, { by: d.approval.approved_by, policy: d.approval.policy });
  sim.emit({ type: "decision", decision: d });
  return d;
}

function runWhatIf(req: WhatIfRequest, delay = true): WhatIfResult {
  const sim = getSim();
  whatifN++;
  const id = `WI-${String(whatifN).padStart(4, "0")}`;
  const done = makeWhatIfResult(id, req, sim.kpis(), sim.tick);
  if (!delay) return done;
  const queued: WhatIfResult = { ...done, status: "queued", runs: [], reference: null, comparison: [], best_strategy: null, narrative: "" };
  whatifs.unshift(queued);
  setTimeout(() => {
    const i = whatifs.findIndex((w) => w.id === id);
    if (i >= 0) whatifs[i] = { ...queued, status: "running" };
  }, 700);
  setTimeout(
    () => {
      const i = whatifs.findIndex((w) => w.id === id);
      if (i >= 0) whatifs[i] = done;
      sim.emit({ type: "whatif", result: done });
    },
    1800 + req.strategies.length * req.seeds * 350,
  );
  return queued;
}

function spatial(): SpatialResponse {
  const sim = getSim();
  const world = sim.world("open", false);
  const nodes: SpatialResponse["nodes"] = [];
  const edges: SpatialResponse["edges"] = [];
  for (const z of world.zones) nodes.push({ id: z.id, kind: "zone", name: z.name, zone_kind: z.kind, closed: z.closed });
  for (const r of world.robots) {
    nodes.push({ id: r.id, kind: "robot", status: r.status, battery: r.battery });
    edges.push({ source: r.id, target: r.zone_id, rel: "is_inside" });
  }
  const adjacency: Record<string, string[]> = {};
  for (const a of world.zones) {
    adjacency[a.id] = [];
    for (const b of world.zones) {
      if (a.id === b.id) continue;
      const touchX = a.x1 + 1 === b.x0 || b.x1 + 1 === a.x0;
      const touchY = a.y1 + 1 === b.y0 || b.y1 + 1 === a.y0;
      const overlapX = a.x0 <= b.x1 && b.x0 <= a.x1;
      const overlapY = a.y0 <= b.y1 && b.y0 <= a.y1;
      if ((touchX && overlapY) || (touchY && overlapX)) {
        adjacency[a.id].push(b.id);
        edges.push({ source: a.id, target: b.id, rel: "adjacent_to" });
      }
    }
  }
  const zone_load: Record<string, Record<string, number>> = {};
  for (const z of world.zones) {
    zone_load[z.id] = {
      robots: world.zone_occupancy[z.id] ?? 0,
      workers: world.workers.filter((w) => w.zone_id === z.id).length,
      orders_requiring: world.orders.filter((o) => o.lines.some((l) => l.shelf_id.startsWith(`${z.id}-`))).length,
      shelves: (sim.shelfIds.filter((s) => s.startsWith(`${z.id}-`)) ?? []).length,
    };
  }
  return { nodes, edges, zone_load, zone_adjacency: adjacency };
}

export const mockApi: NexusApi = {
  async health() {
    const sim = getSim();
    return { status: "ok", version: "mock", tick: sim.tick, llm: sim.status().llm };
  },
  async status() {
    return getSim().status();
  },
  async control(req) {
    const sim = getSim();
    switch (req.action) {
      case "start":
        if (req.autopilot !== undefined && req.autopilot !== null) sim.autopilot = req.autopilot;
        sim.start();
        break;
      case "pause":
        sim.pause();
        break;
      case "step":
        sim.step(req.ticks ?? 1);
        break;
      case "speed":
        if (req.ticks_per_second) sim.setSpeed(req.ticks_per_second);
        if (req.autopilot !== undefined && req.autopilot !== null) sim.autopilot = req.autopilot;
        break;
      case "reset":
        sim.reset({ scale: req.scale, seed: req.seed, strategy: req.strategy, autopilot: req.autopilot });
        decisions.length = 0;
        whatifs.length = 0;
        break;
    }
    return sim.status();
  },
  async world(q) {
    return getSim().world(q?.orders ?? "open", q?.grid ?? true);
  },
  async robots() {
    return getSim().robotModels();
  },
  async orders(q) {
    const list = getSim().openOrders();
    const filtered = q?.status ? list.filter((o) => o.status === q.status) : list;
    return filtered.slice(0, q?.limit ?? 200);
  },
  async entity(id) {
    const sim = getSim();
    const world = sim.world("open", false);
    const robot = world.robots.find((r) => r.id === id);
    if (robot) {
      return { ...robot, relations: { entity_id: id, kind: "robot", triples: [[id, "is_inside", robot.zone_id]], description: [`${id} is_inside ${robot.zone_id}`] } };
    }
    const zone = world.zones.find((z) => z.id === id);
    if (zone) return { ...zone, relations: { entity_id: id, kind: "zone", triples: [], description: [] } };
    const order = world.orders.find((o) => o.id === id);
    if (order) return { ...order, relations: { entity_id: id, kind: "order", triples: order.lines.map((l) => [id, "requires", l.shelf_id]), description: [] } };
    throw new ApiError(404, `entity ${id} not found`, `/api/world/entity/${id}`);
  },
  async kpis() {
    return getSim().kpis();
  },
  async spatial() {
    return spatial();
  },
  async events(q) {
    const sim = getSim();
    let list = sim.events.filter((e) => e.seq > (q?.since_seq ?? 0));
    if (q?.types?.length) list = list.filter((e) => q.types!.includes(e.type));
    return list.slice(0, q?.limit ?? 200);
  },
  async recentEvents(q) {
    return getSim().recentEvents(q?.limit ?? 100, q?.notable ?? false);
  },
  async injectEvent(req) {
    return applyInjected(req);
  },
  async faultPresets() {
    return FAULT_PRESETS;
  },
  async fireFault(presetId) {
    const preset = FAULT_PRESETS.find((p) => p.id === presetId);
    if (!preset) throw new ApiError(404, `unknown fault preset ${presetId}`, `/api/faults/${presetId}`);
    await sleep(120);
    return applyInjected(preset.event);
  },
  async forecast(horizonMin) {
    const sim = getSim();
    await sleep(150);
    return makeForecast(sim.tick, sim.kpis(), sim.robotModels(), sim.zones, sim.zoneOccupancy, sim.demandMultiplier, horizonMin ?? 60);
  },
  async createDecision(req) {
    return createDecision(req.trigger ?? "manual");
  },
  async decisions(limit) {
    return decisions.slice(0, limit ?? 50);
  },
  async decision(id) {
    const d = decisions.find((x) => x.id === id);
    if (!d) throw new ApiError(404, `decision ${id} not found`, `/api/decisions/${id}`);
    return d;
  },
  async decisionAction(id, req) {
    const sim = getSim();
    const i = decisions.findIndex((x) => x.id === id);
    if (i < 0) throw new ApiError(404, `decision ${id} not found`, `/api/decisions/${id}/actions`);
    const d = decisions[i];
    await sleep(300);
    let next: DecisionModel;
    if (req.action === "approve") {
      next = {
        ...d,
        status: "approved",
        recommended_plan_id: req.plan_id ?? d.recommended_plan_id,
        approval: { ...d.approval, policy: "human", auto_approved: false, approved_by: req.actor ?? "operator", approved_tick: sim.tick, reason: req.note || "Approved by operator" },
      };
      sim.planEvent("PLAN_APPROVED", d.id, { by: req.actor ?? "operator" });
    } else if (req.action === "reject") {
      next = { ...d, status: "rejected", approval: { ...d.approval, policy: "human", auto_approved: false, reason: req.note || "Rejected by operator" } };
      sim.planEvent("PLAN_REJECTED", d.id, { by: req.actor ?? "operator" });
    } else {
      if (d.status !== "approved") throw new ApiError(409, "decision must be approved before execution", `/api/decisions/${id}/actions`);
      next = { ...d, status: "executed" };
      sim.planEvent("PLAN_EXECUTED", d.id, { plan_id: d.recommended_plan_id, actions: 3 });
      sim.pushEvent("TASK_REASSIGNED", "R07", { to_robots: ["R03", "R09"], tasks: 14 }, "agent");
    }
    decisions[i] = next;
    sim.emit({ type: "decision", decision: next });
    return next;
  },
  async createWhatIf(req) {
    await sleep(200);
    return runWhatIf(req);
  },
  async whatifs() {
    return whatifs.slice();
  },
  async whatif(id) {
    const w = whatifs.find((x) => x.id === id);
    if (!w) throw new ApiError(404, `what-if ${id} not found`, `/api/whatif/${id}`);
    return w;
  },
  async whatifPresets() {
    return WHATIF_PRESETS;
  },
  async nlq(req) {
    const sim = getSim();
    await sleep(350 + Math.random() * 500);
    const forecast = makeForecast(sim.tick, sim.kpis(), sim.robotModels(), sim.zones, sim.zoneOccupancy, sim.demandMultiplier, req.horizon_min ?? 60);
    return answerNlq(req.question, {
      kpis: sim.kpis(),
      robots: sim.robotModels(),
      zones: sim.zones.map((z) => ({ ...z, closed: sim.closedZones.has(z.id) })),
      occupancy: sim.zoneOccupancy,
      recentNotable: sim
        .recentEvents(50, true)
        .map((e) => `${e.type}${e.entity_id ? ` ${e.entity_id}` : ""}`),
      forecast,
      runWhatIf: (r) => {
        const result = runWhatIf(r, false);
        whatifs.unshift(result);
        return result;
      },
    });
  },
  async timeline(q) {
    const sim = getSim();
    const from = q?.from_tick ?? 0;
    const to = q?.to_tick ?? Number.MAX_SAFE_INTEGER;
    return {
      points: sim.timeline.filter((p) => p.tick >= from && p.tick <= to),
      snapshots: sim.snapshotInfos().filter((s) => s.tick >= from && s.tick <= to),
      notable_events: sim.events.filter((e) => NOTABLE_EVENT_TYPES.has(e.type) && e.tick >= from && e.tick <= to),
    };
  },
  async snapshot(tick) {
    const w = getSim().snapshot(tick);
    if (!w) throw new ApiError(404, `no snapshot at tick ${tick}`, `/api/snapshots/${tick}`);
    return w;
  },
  async benchmarks() {
    await sleep(120);
    return BENCHMARKS;
  },
  async strategies() {
    return STRATEGIES;
  },
};
