/**
 * In-browser mock of the NEXUS engine, used when `NEXT_PUBLIC_MOCK=1`.
 *
 * It animates the generated `world.small.json` fixture: robots pick orders, walk BFS paths on the
 * walkable grid, deliver to docks, charge when low and fail on demand. KPIs, events, a KPI timeline
 * and snapshots are synthesised so every page renders with realistic data without a backend.
 */
import { bfsPath, buildZoneIndex, cellKey, isWalkable, walkableCells } from "@/lib/grid";
import type {
  Cell,
  EventModel,
  KPIModel,
  OrderLineModel,
  OrderModel,
  OrderPriorityName,
  RobotModel,
  RobotStatus,
  ServerFrame,
  SimStatus,
  SnapshotInfo,
  TaskModel,
  TickFrame,
  TimelinePoint,
  WaypointModel,
  WorldSnapshot,
  WorkerModel,
  ZoneModel,
} from "@/lib/types";
import { NOTABLE_EVENT_TYPES } from "@/lib/colors";
import raw from "./world.small.json";

const BASE = raw as unknown as WorldSnapshot;
const EPOCH_MS = Date.UTC(2026, 0, 5, 8, 0, 0);
const PRIORITY_NAMES: OrderPriorityName[] = ["LOW", "NORMAL", "HIGH", "CRITICAL"];

export function simTimeIso(tick: number): string {
  return new Date(EPOCH_MS + tick * 1000).toISOString().replace(".000Z", "+00:00");
}

export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

type Mode = "idle" | "to_shelf" | "picking" | "to_dock" | "unloading" | "to_charger" | "charging" | "failed";

interface MRobot {
  id: string;
  cell: Cell;
  path: Cell[];
  target: Cell | null;
  mode: Mode;
  waiting: boolean;
  battery: number;
  orderId: string | null;
  taskId: string | null;
  lineIdx: number;
  phaseUntil: number;
  load: number;
  distance: number;
  energy: number;
  failureCause: string | null;
  failedTick: number | null;
  recoverAt: number | null;
  chargerId: string | null;
  targetDock: string | null;
  tasksCompleted: number;
  ordersCompleted: number;
  productiveTicks: number;
  operationalTicks: number;
  waitTicks: number;
  zoneId: string;
  taskCreated: number;
}

export interface ResetOptions {
  scale?: string | null;
  seed?: number | null;
  strategy?: string | null;
  autopilot?: boolean | null;
}

export type FrameListener = (frame: ServerFrame) => void;

interface SnapshotRecord extends SnapshotInfo {
  robots: RobotModel[];
  zone_occupancy: Record<string, number>;
}

export class MockSim {
  readonly grid = BASE.grid!;
  readonly zones: ZoneModel[] = BASE.zones;
  readonly zoneIdx = buildZoneIndex(BASE.zones, BASE.grid!);
  readonly walkable: Cell[] = walkableCells(BASE.grid!);
  readonly shelfAccess = new Map<string, Cell>();
  readonly shelfIds: string[] = [];
  readonly zoneById = new Map<string, ZoneModel>();

  tick = 0;
  running = false;
  tps = 10;
  strategy = "nexus_full";
  autopilot = false;
  scale = "small";
  seed = 42;
  startedAt = Date.now();
  demandMultiplier = 1;
  burstUntil = 0;
  burstMultiplier = 1;

  robots = new Map<string, MRobot>();
  orders = new Map<string, OrderModel>();
  blocked = new Set<number>();
  closedZones = new Set<string>();
  dockOpen = new Map<string, boolean>();
  dockQueue = new Map<string, string[]>();
  dockDelivered = new Map<string, number>();
  chargerEnabled = new Map<string, boolean>();
  chargerOccupants = new Map<string, string[]>();
  workers: WorkerModel[] = [];
  events: EventModel[] = [];
  timeline: TimelinePoint[] = [];
  snapshots: SnapshotRecord[] = [];
  zoneOccupancy: Record<string, number> = {};

  private seq = 0;
  private orderN = 0;
  private taskN = 0;
  private eventN = 0;
  private rng = mulberry32(42);
  private listeners = new Set<FrameListener>();
  private timer: ReturnType<typeof setInterval> | null = null;
  private tickAccumulator = 0;
  private occupancy = new Map<number, number>();
  private deliveredTicks: number[] = [];
  private lateTicks: number[] = [];

  // stats
  created = 0;
  delivered = 0;
  late = 0;
  cancelled = 0;
  fulfillmentTotal = 0;
  latenessTotal = 0;
  fulfillmentSamples: number[] = [];
  distanceTotal = 0;
  energyTotal = 0;
  replans = 0;
  failures = 0;
  chargingSessions = 0;
  waitTotal = 0;
  productiveTotal = 0;
  operationalTotal = 0;
  congestionEma = 0;
  utilizationEma = 0;
  congestionTotal = 0;

  constructor() {
    for (const s of BASE.shelves ?? []) {
      this.shelfAccess.set(s.id, s.access_cell);
      this.shelfIds.push(s.id);
    }
    for (const z of this.zones) this.zoneById.set(z.id, z);
    this.reset({});
  }

  // ---- lifecycle -------------------------------------------------------------------------------

  reset(opts: ResetOptions): void {
    this.pause();
    this.scale = opts.scale ?? this.scale;
    this.seed = opts.seed ?? this.seed;
    this.strategy = opts.strategy ?? this.strategy;
    this.autopilot = opts.autopilot ?? this.autopilot;
    this.rng = mulberry32(this.seed);
    this.tick = 0;
    this.startedAt = Date.now();
    this.demandMultiplier = 1;
    this.burstUntil = 0;
    this.burstMultiplier = 1;
    this.robots.clear();
    this.orders.clear();
    this.blocked.clear();
    this.closedZones.clear();
    this.events = [];
    this.timeline = [];
    this.snapshots = [];
    this.seq = 0;
    this.orderN = 0;
    this.taskN = 0;
    this.eventN = 0;
    this.created = this.delivered = this.late = this.cancelled = 0;
    this.fulfillmentTotal = this.latenessTotal = 0;
    this.fulfillmentSamples = [];
    this.distanceTotal = this.energyTotal = 0;
    this.replans = this.failures = this.chargingSessions = 0;
    this.waitTotal = this.productiveTotal = this.operationalTotal = 0;
    this.congestionEma = this.utilizationEma = this.congestionTotal = 0;
    this.deliveredTicks = [];
    this.lateTicks = [];
    for (const d of BASE.docks) {
      this.dockOpen.set(d.id, true);
      this.dockQueue.set(d.id, []);
      this.dockDelivered.set(d.id, 0);
    }
    for (const c of BASE.chargers) {
      this.chargerEnabled.set(c.id, true);
      this.chargerOccupants.set(c.id, []);
    }
    this.workers = BASE.workers.map((w) => ({ ...w, status: "available", delay_until_tick: 0 }));
    for (const r of BASE.robots) {
      this.robots.set(r.id, {
        id: r.id,
        cell: [r.cell[0], r.cell[1]],
        path: [],
        target: null,
        mode: "idle",
        waiting: false,
        battery: r.battery,
        orderId: null,
        taskId: null,
        lineIdx: 0,
        phaseUntil: 0,
        load: 0,
        distance: 0,
        energy: 0,
        failureCause: null,
        failedTick: null,
        recoverAt: null,
        chargerId: null,
        targetDock: null,
        tasksCompleted: 0,
        ordersCompleted: 0,
        productiveTicks: 0,
        operationalTicks: 0,
        waitTicks: 0,
        zoneId: r.zone_id,
        taskCreated: 0,
      });
    }
    // seed a realistic backlog and a bit of history so charts are not empty
    for (let i = 0; i < 14; i++) this.createOrder();
    this.recomputeOccupancy();
    this.advance(180); // warm-up so the first frames already show traffic and a short KPI history
    this.emit({ type: "status", status: this.status() });
  }

  start(): void {
    if (this.running) return;
    this.running = true;
    this.timer = setInterval(() => this.pump(), 50);
    this.emit({ type: "status", status: this.status() });
  }

  pause(): void {
    this.running = false;
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this.emit({ type: "status", status: this.status() });
  }

  setSpeed(tps: number): void {
    this.tps = Math.max(0.5, Math.min(1000, tps));
    this.emit({ type: "status", status: this.status() });
  }

  step(n: number): void {
    this.advance(n);
    this.emit(this.frame());
    this.emit({ type: "status", status: this.status() });
  }

  subscribe(cb: FrameListener): () => void {
    this.listeners.add(cb);
    return () => this.listeners.delete(cb);
  }

  emit(frame: ServerFrame): void {
    for (const cb of this.listeners) cb(frame);
  }

  private pump(): void {
    this.tickAccumulator += this.tps / 20;
    const n = Math.floor(this.tickAccumulator);
    if (n <= 0) return;
    this.tickAccumulator -= n;
    this.advance(n);
    this.emit(this.frame());
  }

  // ---- engine ----------------------------------------------------------------------------------

  advance(n: number): void {
    for (let i = 0; i < n; i++) this.tickOnce();
  }

  private tickOnce(): void {
    this.tick++;
    this.spawnOrders();
    this.recomputeOccupancy();
    let productive = 0;
    let operational = 0;
    for (const r of this.robots.values()) {
      this.updateRobot(r);
      if (r.mode !== "failed") {
        operational++;
        r.operationalTicks++;
        if (r.mode === "to_shelf" || r.mode === "to_dock" || r.mode === "picking" || r.mode === "unloading") {
          productive++;
          r.productiveTicks++;
        }
      }
    }
    this.productiveTotal += productive;
    this.operationalTotal += operational;
    this.recomputeOccupancy();
    const congestion = this.instantCongestion();
    this.congestionTotal += congestion;
    this.congestionEma += (congestion - this.congestionEma) * 0.02;
    const util = operational ? productive / operational : 0;
    this.utilizationEma += (util - this.utilizationEma) * 0.02;
    for (const w of this.workers) {
      if (w.status === "delayed" && this.tick >= w.delay_until_tick) w.status = "available";
    }
    if (this.tick % 60 === 0) {
      const k = this.kpis();
      this.timeline.push({
        tick: this.tick,
        open: k.orders_open,
        delivered: k.orders_delivered,
        breach_projected: k.sla_breach_rate_projected,
        congestion: k.congestion_index,
        utilization: k.robot_utilization,
      });
      if (this.timeline.length > 2000) this.timeline.shift();
    }
    if (this.tick % 600 === 0) this.takeSnapshot();
  }

  private spawnOrders(): void {
    const hour = new Date(EPOCH_MS + this.tick * 1000).getUTCHours();
    let rate = (BASE.demand.orders_per_hour * BASE.demand.hourly_multipliers[hour % 24] * this.demandMultiplier) / 3600;
    if (this.tick < this.burstUntil) rate *= this.burstMultiplier;
    // keep the backlog bounded so the mock stays readable
    if (this.openOrders().length > 80) rate *= 0.3;
    if (this.rng() < rate) this.createOrder();
  }

  private createOrder(): OrderModel {
    this.orderN++;
    this.created++;
    const w = BASE.demand.priority_weights;
    const u = this.rng();
    let acc = 0;
    let priority = 1;
    for (let i = 0; i < w.length; i++) {
      acc += w[i];
      if (u < acc) {
        priority = i;
        break;
      }
    }
    const nLines = 1 + Math.floor(this.rng() * Math.min(3, BASE.demand.max_lines));
    const lines: OrderLineModel[] = [];
    for (let i = 0; i < nLines; i++) {
      const shelfId = this.shelfIds[Math.floor(this.rng() * this.shelfIds.length)];
      const shelf = (BASE.shelves ?? []).find((s) => s.id === shelfId);
      const skus = shelf ? Object.keys(shelf.inventory) : ["SKU-00001"];
      lines.push({ sku: skus[Math.floor(this.rng() * skus.length)], qty: 1 + Math.floor(this.rng() * 2), shelf_id: shelfId, picked: false });
    }
    const pname = PRIORITY_NAMES[priority];
    const sla = BASE.config.sla_minutes[pname] ?? 10;
    const order: OrderModel = {
      id: `ORD-${String(this.orderN).padStart(6, "0")}`,
      created_tick: this.tick,
      deadline_tick: this.tick + Math.round(sla * 60),
      priority,
      priority_name: pname,
      lines,
      status: "pending",
      task_id: null,
      robot_id: null,
      dock_id: null,
      started_tick: null,
      delivered_tick: null,
      cancelled_tick: null,
      items: lines.reduce((a, l) => a + l.qty, 0),
    };
    this.orders.set(order.id, order);
    this.pushEvent("ORDER_CREATED", order.id, { priority: pname, lines: lines.length });
    return order;
  }

  private pendingQueue(): OrderModel[] {
    const pending = [...this.orders.values()].filter((o) => o.status === "pending");
    if (this.strategy === "baseline") return pending.sort((a, b) => a.created_tick - b.created_tick);
    return pending.sort((a, b) => b.priority - a.priority || a.deadline_tick - b.deadline_tick);
  }

  private cellCapacity(x: number, y: number): number {
    const t = this.grid.rows[y]?.[x] ?? "2";
    if (t === "3" || t === "4" || t === "6") return 99;
    const z = this.zoneIdx[y * this.grid.width + x];
    const zone = z ? this.zoneById.get(z) : undefined;
    return zone?.kind === "corridor" ? BASE.config.corridor_cell_capacity : BASE.config.cell_capacity;
  }

  private recomputeOccupancy(): void {
    this.occupancy.clear();
    const zo: Record<string, number> = {};
    for (const r of this.robots.values()) {
      const k = cellKey(r.cell[0], r.cell[1]);
      this.occupancy.set(k, (this.occupancy.get(k) ?? 0) + 1);
      const z = this.zoneIdx[r.cell[1] * this.grid.width + r.cell[0]] ?? r.zoneId;
      r.zoneId = z;
      zo[z] = (zo[z] ?? 0) + 1;
    }
    this.zoneOccupancy = zo;
  }

  private instantCongestion(): number {
    let total = 0;
    for (const z of this.zones) {
      if (z.kind !== "storage" && z.kind !== "corridor") continue;
      total += Math.max(0, (this.zoneOccupancy[z.id] ?? 0) - z.capacity);
    }
    return total;
  }

  private plan(r: MRobot, to: Cell): boolean {
    const path = bfsPath(this.grid, this.blocked, this.closedZones, this.zoneIdx, r.cell, to);
    if (!path) return false;
    r.path = path;
    r.target = to;
    return true;
  }

  private nearestOpenDock(from: Cell): { id: string; cell: Cell } | null {
    let best: { id: string; cell: Cell } | null = null;
    let bestD = Infinity;
    for (const d of BASE.docks) {
      if (!this.dockOpen.get(d.id)) continue;
      const dist = Math.abs(d.cell[0] - from[0]) + Math.abs(d.cell[1] - from[1]) + (this.dockQueue.get(d.id)?.length ?? 0) * 6;
      if (dist < bestD) {
        bestD = dist;
        best = { id: d.id, cell: d.cell };
      }
    }
    return best;
  }

  private freeCharger(from: Cell): { id: string; cell: Cell } | null {
    let best: { id: string; cell: Cell } | null = null;
    let bestD = Infinity;
    for (const c of BASE.chargers) {
      if (!this.chargerEnabled.get(c.id)) continue;
      if ((this.chargerOccupants.get(c.id)?.length ?? 0) >= c.slots) continue;
      const dist = Math.abs(c.cell[0] - from[0]) + Math.abs(c.cell[1] - from[1]);
      if (dist < bestD) {
        bestD = dist;
        best = { id: c.id, cell: c.cell };
      }
    }
    return best;
  }

  private releaseOrder(r: MRobot): void {
    if (!r.orderId) return;
    const o = this.orders.get(r.orderId);
    if (o) {
      o.status = "pending";
      o.robot_id = null;
      o.task_id = null;
      o.dock_id = null;
      o.started_tick = null;
      for (const l of o.lines) l.picked = false;
      this.pushEvent("TASK_CANCELLED", r.taskId, { order_id: o.id, reason: "robot unavailable" });
    }
    r.orderId = null;
    r.taskId = null;
    r.lineIdx = 0;
    r.load = 0;
    r.targetDock = null;
    r.path = [];
    r.target = null;
  }

  private moveAlong(r: MRobot): void {
    if (r.path.length === 0) return;
    const next = r.path[0];
    if (!isWalkable(this.grid, this.blocked, this.closedZones, this.zoneIdx, next[0], next[1])) {
      this.replans++;
      if (!r.target || !this.plan(r, r.target)) {
        r.waiting = true;
        r.waitTicks++;
        this.waitTotal++;
        return;
      }
      return;
    }
    const k = cellKey(next[0], next[1]);
    if ((this.occupancy.get(k) ?? 0) >= this.cellCapacity(next[0], next[1])) {
      r.waiting = true;
      r.waitTicks++;
      this.waitTotal++;
      return;
    }
    r.waiting = false;
    const prevK = cellKey(r.cell[0], r.cell[1]);
    this.occupancy.set(prevK, Math.max(0, (this.occupancy.get(prevK) ?? 1) - 1));
    this.occupancy.set(k, (this.occupancy.get(k) ?? 0) + 1);
    r.cell = next;
    r.path.shift();
    r.distance++;
    this.distanceTotal++;
    const drain = BASE.config.battery_drain_move * 2.5;
    r.battery = Math.max(0, r.battery - drain);
    r.energy += drain;
    this.energyTotal += drain;
  }

  private updateRobot(r: MRobot): void {
    switch (r.mode) {
      case "failed":
        if (r.recoverAt !== null && this.tick >= r.recoverAt) {
          r.mode = "idle";
          r.failureCause = null;
          r.failedTick = null;
          r.recoverAt = null;
          r.phaseUntil = this.tick + 5;
          this.pushEvent("ROBOT_RECOVERED", r.id, {});
        }
        return;
      case "charging": {
        r.battery = Math.min(100, r.battery + BASE.config.battery_charge_rate * 2);
        if (r.battery >= BASE.config.battery_charge_target) {
          if (r.chargerId) {
            const occ = this.chargerOccupants.get(r.chargerId) ?? [];
            this.chargerOccupants.set(r.chargerId, occ.filter((x) => x !== r.id));
          }
          r.chargerId = null;
          r.mode = "idle";
          r.phaseUntil = this.tick + 3;
        }
        return;
      }
      case "to_charger": {
        if (r.path.length === 0) {
          r.mode = "charging";
          if (r.chargerId) {
            const occ = this.chargerOccupants.get(r.chargerId) ?? [];
            if (!occ.includes(r.id)) this.chargerOccupants.set(r.chargerId, [...occ, r.id]);
          }
          this.chargingSessions++;
          return;
        }
        this.moveAlong(r);
        return;
      }
      case "idle": {
        r.battery = Math.max(0, r.battery - BASE.config.battery_drain_idle);
        if (r.battery < BASE.config.battery_low_threshold) {
          const ch = this.freeCharger(r.cell);
          if (ch && this.plan(r, ch.cell)) {
            r.mode = "to_charger";
            r.chargerId = ch.id;
            this.pushEvent("BATTERY_LOW", r.id, { battery: Math.round(r.battery * 10) / 10, charger_id: ch.id });
            return;
          }
        }
        if (this.tick < r.phaseUntil) return;
        const queue = this.pendingQueue();
        const order = queue[0];
        if (!order) return;
        const access = this.shelfAccess.get(order.lines[0].shelf_id);
        if (!access || !this.plan(r, access)) {
          r.phaseUntil = this.tick + 10;
          return;
        }
        this.taskN++;
        r.taskId = `TASK-${String(this.taskN).padStart(6, "0")}`;
        r.taskCreated = this.tick;
        r.orderId = order.id;
        r.lineIdx = 0;
        r.mode = "to_shelf";
        order.status = "assigned";
        order.robot_id = r.id;
        order.task_id = r.taskId;
        order.started_tick = this.tick;
        this.pushEvent("TASK_CREATED", r.id, { task_id: r.taskId, order_ids: [order.id], origin: this.strategy });
        this.pushEvent("ORDER_ASSIGNED", order.id, { robot_id: r.id, task_id: r.taskId });
        return;
      }
      case "to_shelf": {
        const order = r.orderId ? this.orders.get(r.orderId) : undefined;
        if (!order) {
          r.mode = "idle";
          return;
        }
        if (order.status === "assigned") order.status = "in_progress";
        if (r.path.length === 0) {
          r.mode = "picking";
          r.phaseUntil = this.tick + BASE.config.pick_ticks;
          return;
        }
        this.moveAlong(r);
        return;
      }
      case "picking": {
        r.battery = Math.max(0, r.battery - BASE.config.battery_drain_action);
        if (this.tick < r.phaseUntil) return;
        const order = r.orderId ? this.orders.get(r.orderId) : undefined;
        if (!order) {
          r.mode = "idle";
          return;
        }
        const line = order.lines[r.lineIdx];
        if (line) {
          line.picked = true;
          r.load += line.qty;
        }
        r.lineIdx++;
        const nextLine = order.lines[r.lineIdx];
        if (nextLine) {
          const access = this.shelfAccess.get(nextLine.shelf_id);
          if (access && this.plan(r, access)) {
            r.mode = "to_shelf";
            return;
          }
        }
        const dock = this.nearestOpenDock(r.cell);
        if (dock && this.plan(r, dock.cell)) {
          r.mode = "to_dock";
          r.targetDock = dock.id;
          order.dock_id = dock.id;
        } else {
          r.phaseUntil = this.tick + 10;
        }
        return;
      }
      case "to_dock": {
        if (r.targetDock && !this.dockOpen.get(r.targetDock)) {
          const dock = this.nearestOpenDock(r.cell);
          if (dock && this.plan(r, dock.cell)) {
            r.targetDock = dock.id;
            const o = r.orderId ? this.orders.get(r.orderId) : undefined;
            if (o) o.dock_id = dock.id;
            this.replans++;
          }
        }
        if (r.path.length === 0) {
          r.mode = "unloading";
          r.phaseUntil = this.tick + BASE.config.unload_ticks;
          if (r.targetDock) {
            const q = this.dockQueue.get(r.targetDock) ?? [];
            this.dockQueue.set(r.targetDock, [...q, r.id]);
          }
          return;
        }
        this.moveAlong(r);
        return;
      }
      case "unloading": {
        r.battery = Math.max(0, r.battery - BASE.config.battery_drain_action);
        if (this.tick < r.phaseUntil) return;
        if (r.targetDock) {
          const q = this.dockQueue.get(r.targetDock) ?? [];
          this.dockQueue.set(r.targetDock, q.filter((x) => x !== r.id));
          this.dockDelivered.set(r.targetDock, (this.dockDelivered.get(r.targetDock) ?? 0) + 1);
        }
        const order = r.orderId ? this.orders.get(r.orderId) : undefined;
        if (order) {
          order.status = "delivered";
          order.delivered_tick = this.tick;
          this.orders.delete(order.id);
          this.delivered++;
          this.deliveredTicks.push(this.tick);
          const ft = this.tick - order.created_tick;
          this.fulfillmentTotal += ft;
          this.fulfillmentSamples.push(ft);
          if (this.fulfillmentSamples.length > 400) this.fulfillmentSamples.shift();
          if (this.tick > order.deadline_tick) {
            this.late++;
            this.lateTicks.push(this.tick);
            this.latenessTotal += this.tick - order.deadline_tick;
          }
          r.ordersCompleted++;
          this.pushEvent("ORDER_DELIVERED", order.id, {
            robot_id: r.id,
            dock_id: r.targetDock,
            late: this.tick > order.deadline_tick,
            fulfillment_min: Math.round(ft / 6) / 10,
          });
        }
        this.pushEvent("TASK_COMPLETED", r.id, { task_id: r.taskId });
        r.tasksCompleted++;
        r.orderId = null;
        r.taskId = null;
        r.lineIdx = 0;
        r.load = 0;
        r.targetDock = null;
        r.mode = "idle";
        r.phaseUntil = this.tick + 2;
        return;
      }
    }
  }

  // ---- faults ----------------------------------------------------------------------------------

  failRobot(id: string, cause = "motor_fault", recoveryMin = 45): EventModel | null {
    const r = this.robots.get(id);
    if (!r) return null;
    this.releaseOrder(r);
    if (r.chargerId) {
      const occ = this.chargerOccupants.get(r.chargerId) ?? [];
      this.chargerOccupants.set(r.chargerId, occ.filter((x) => x !== r.id));
      r.chargerId = null;
    }
    r.mode = "failed";
    r.waiting = false;
    r.failureCause = cause;
    r.failedTick = this.tick;
    r.recoverAt = this.tick + recoveryMin * 60;
    this.failures++;
    return this.pushEvent("ROBOT_FAILURE", id, { cause, recovery_ticks: recoveryMin * 60 }, "user");
  }

  blockAisle(zoneId: string, cells?: Cell[]): EventModel {
    const zone = this.zoneById.get(zoneId);
    const targets: Cell[] = cells ?? [];
    if (!cells && zone) {
      const x = zone.x0 + 3;
      for (let y = zone.y0 + 2; y <= zone.y0 + 7; y++) {
        if ((this.grid.rows[y]?.[x] ?? "2") === "0") targets.push([x, y]);
      }
    }
    for (const [x, y] of targets) this.blocked.add(cellKey(x, y));
    return this.pushEvent("AISLE_BLOCKED", zoneId, { cells: targets, reason: "spill" }, "user");
  }

  clearAisles(): EventModel {
    this.blocked.clear();
    return this.pushEvent("AISLE_CLEARED", null, {}, "user");
  }

  closeZone(zoneId: string): EventModel {
    this.closedZones.add(zoneId);
    for (const r of this.robots.values()) {
      if (r.zoneId === zoneId && r.mode !== "failed") {
        this.releaseOrder(r);
        r.mode = "idle";
      }
    }
    return this.pushEvent("ZONE_CLOSED", zoneId, {}, "user");
  }

  openZone(zoneId: string): EventModel {
    this.closedZones.delete(zoneId);
    return this.pushEvent("ZONE_OPENED", zoneId, {}, "user");
  }

  closeDock(dockId: string): EventModel {
    this.dockOpen.set(dockId, false);
    return this.pushEvent("DOCK_CLOSED", dockId, {}, "user");
  }

  openDock(dockId: string): EventModel {
    this.dockOpen.set(dockId, true);
    return this.pushEvent("DOCK_OPENED", dockId, {}, "user");
  }

  changeDemand(multiplier: number): EventModel {
    this.demandMultiplier = multiplier;
    return this.pushEvent("DEMAND_CHANGED", null, { multiplier }, "user");
  }

  burst(multiplier: number, durationMin: number): EventModel {
    this.burstMultiplier = multiplier;
    this.burstUntil = this.tick + durationMin * 60;
    return this.pushEvent("DEMAND_CHANGED", null, { burst_multiplier: multiplier, duration_min: durationMin }, "user");
  }

  disableCharger(id: string): EventModel {
    this.chargerEnabled.set(id, false);
    return this.pushEvent("CHARGER_DISABLED", id, {}, "user");
  }

  delayWorker(id: string, minutes: number): EventModel {
    const w = this.workers.find((x) => x.id === id);
    if (w) {
      w.status = "delayed";
      w.delay_until_tick = this.tick + minutes * 60;
    }
    return this.pushEvent("WORKER_DELAY", id, { minutes }, "user");
  }

  planEvent(type: string, decisionId: string, payload: Record<string, unknown> = {}): EventModel {
    return this.pushEvent(type, decisionId, payload, "agent");
  }

  // ---- events ----------------------------------------------------------------------------------

  pushEvent(type: string, entityId: string | null, payload: Record<string, unknown>, origin = "engine"): EventModel {
    this.seq++;
    this.eventN++;
    const ev: EventModel = {
      id: `EV-${String(this.eventN).padStart(7, "0")}`,
      seq: this.seq,
      type,
      tick: this.tick,
      entity_id: entityId,
      payload,
      origin,
      cause: null,
      ephemeral: false,
    };
    this.events.push(ev);
    if (this.events.length > 600) this.events.shift();
    this.emit({ type: "event", event: ev });
    return ev;
  }

  recentEvents(limit = 100, notable = false): EventModel[] {
    const list = notable ? this.events.filter((e) => NOTABLE_EVENT_TYPES.has(e.type)) : this.events;
    return list.slice(-limit);
  }

  // ---- projections -----------------------------------------------------------------------------

  openOrders(): OrderModel[] {
    return [...this.orders.values()].sort((a, b) => a.created_tick - b.created_tick);
  }

  robotStatus(r: MRobot): RobotStatus {
    switch (r.mode) {
      case "idle":
        return "idle";
      case "to_shelf":
        return r.waiting ? "waiting" : "moving";
      case "picking":
        return "picking";
      case "to_dock":
        return r.waiting ? "waiting" : "delivering";
      case "unloading":
        return "unloading";
      case "to_charger":
        return "to_charger";
      case "charging":
        return "charging";
      case "failed":
        return "failed";
    }
  }

  robotModel(r: MRobot): RobotModel {
    return {
      id: r.id,
      cell: [r.cell[0], r.cell[1]],
      zone_id: r.zoneId,
      battery: Math.round(r.battery * 100) / 100,
      status: this.robotStatus(r),
      task_id: r.taskId,
      path: r.path.slice(0, 64).map((c) => [c[0], c[1]] as Cell),
      speed: 1,
      capacity: 10,
      load: r.load,
      action_until_tick: r.phaseUntil,
      wait_ticks: r.waiting ? r.waitTicks : 0,
      distance: r.distance,
      energy: Math.round(r.energy * 1000) / 1000,
      productive_ticks: r.productiveTicks,
      operational_ticks: r.operationalTicks,
      failure_cause: r.failureCause,
      failed_tick: r.failedTick,
      recover_at_tick: r.recoverAt,
      charger_id: r.chargerId,
      tasks_completed: r.tasksCompleted,
      orders_completed: r.ordersCompleted,
    };
  }

  robotModels(): RobotModel[] {
    return [...this.robots.values()].map((r) => this.robotModel(r));
  }

  tasks(): TaskModel[] {
    const out: TaskModel[] = [];
    for (const r of this.robots.values()) {
      if (!r.taskId || !r.orderId) continue;
      const o = this.orders.get(r.orderId);
      if (!o) continue;
      const waypoints: WaypointModel[] = o.lines.map((l) => ({
        kind: "pick",
        target_id: l.shelf_id,
        cell: this.shelfAccess.get(l.shelf_id) ?? r.cell,
        order_id: o.id,
        done: l.picked,
      }));
      const dock = BASE.docks.find((d) => d.id === (r.targetDock ?? o.dock_id));
      waypoints.push({
        kind: "deliver",
        target_id: dock?.id ?? "",
        cell: dock?.cell ?? r.cell,
        order_id: o.id,
        done: false,
      });
      out.push({
        id: r.taskId,
        robot_id: r.id,
        order_ids: [o.id],
        waypoints,
        created_tick: r.taskCreated,
        status: "active",
        leg: Math.min(r.lineIdx, waypoints.length - 1),
        completed_tick: null,
        origin: this.strategy,
      });
    }
    return out;
  }

  kpis(): KPIModel {
    const open = this.openOrders();
    const overdue = open.filter((o) => o.deadline_tick < this.tick).length;
    const atRisk = open.filter((o) => o.deadline_tick >= this.tick && o.deadline_tick - this.tick < 180).length;
    const window = Math.min(this.tick, 1800);
    const cutoff = this.tick - window;
    const recentDelivered = this.deliveredTicks.filter((t) => t > cutoff).length;
    const recentLate = this.lateTicks.filter((t) => t > cutoff).length;
    const throughput = window >= 120 ? (recentDelivered * 3600) / window : 0;
    const breach = this.delivered ? this.late / this.delivered : 0;
    const recentBreach = recentDelivered ? recentLate / recentDelivered : breach;
    const projected = Math.min(
      1,
      Math.max(0, (recentLate + overdue + 0.5 * atRisk) / Math.max(1, recentDelivered + open.length) * 0.6 + recentBreach * 0.4),
    );
    const samples = [...this.fulfillmentSamples].sort((a, b) => a - b);
    const q = (p: number) => (samples.length ? samples[Math.min(samples.length - 1, Math.floor(p * samples.length))] / 60 : 0);
    const robots = [...this.robots.values()];
    const operational = robots.filter((r) => r.mode !== "failed").length;
    const robotHours = (this.operationalTotal || 1) / 3600;
    return {
      tick: this.tick,
      sim_hours: Math.round((this.tick / 3600) * 1000) / 1000,
      orders_created: this.created,
      orders_delivered: this.delivered,
      orders_open: open.length,
      orders_pending: open.filter((o) => o.status === "pending").length,
      orders_late: this.late,
      orders_overdue_open: overdue,
      orders_cancelled: this.cancelled,
      avg_fulfillment_min: this.delivered ? Math.round((this.fulfillmentTotal / this.delivered / 60) * 100) / 100 : 0,
      p50_fulfillment_min: Math.round(q(0.5) * 100) / 100,
      p95_fulfillment_min: Math.round(q(0.95) * 100) / 100,
      sla_breach_rate: Math.round(breach * 10000) / 10000,
      sla_breach_rate_projected: Math.round(projected * 10000) / 10000,
      throughput_per_hour: Math.round(throughput * 10) / 10,
      robot_utilization: Math.round(this.utilizationEma * 1000) / 1000,
      robot_availability: robots.length ? operational / robots.length : 0,
      robots_total: robots.length,
      robots_operational: operational,
      distance_total: this.distanceTotal,
      energy_total: Math.round(this.energyTotal * 100) / 100,
      congestion_index: Math.round(this.congestionEma * 1000) / 1000,
      wait_ticks_per_robot_hour: Math.round((this.waitTotal / robotHours) * 10) / 10,
      replans: this.replans,
      failures: this.failures,
      charging_sessions: this.chargingSessions,
      inventory_units: BASE.summary.inventory_units,
      avg_lateness_min: this.late ? Math.round((this.latenessTotal / this.late / 60) * 100) / 100 : 0,
    };
  }

  status(): SimStatus {
    return {
      running: this.running,
      tick: this.tick,
      sim_time: simTimeIso(this.tick),
      ticks_per_second: this.tps,
      strategy: this.strategy,
      scale: this.scale,
      seed: this.seed,
      domain: "warehouse",
      autopilot: this.autopilot,
      events_persisted: this.seq,
      decisions: 0,
      llm: { enabled: false, model: "mock", available: false, url: "mock://" },
      uptime_s: Math.round((Date.now() - this.startedAt) / 1000),
    };
  }

  world(orders: "open" | "all" | "none" = "open", includeGrid = true): WorldSnapshot {
    const robots = this.robotModels();
    const open = this.openOrders();
    const operational = robots.filter((r) => r.status !== "failed" && r.status !== "maintenance").length;
    const snap: WorldSnapshot = {
      summary: {
        ...BASE.summary,
        scale: this.scale,
        seed: this.seed,
        tick: this.tick,
        sim_time: simTimeIso(this.tick),
        version: this.seq,
        robots_total: robots.length,
        robots_operational: operational,
        robots_failed: robots.length - operational,
        robots_charging: robots.filter((r) => r.status === "charging").length,
        orders_open: open.length,
        orders_pending: open.filter((o) => o.status === "pending").length,
        orders_delivered: this.delivered,
        orders_late: this.late,
        tasks_active: this.tasks().length,
        congestion: this.instantCongestion(),
        blocked_cells: this.blocked.size,
        closed_zones: [...this.closedZones].sort(),
        labels: { strategy: this.strategy },
      },
      clock: { tick: this.tick, tick_seconds: 1, sim_time: simTimeIso(this.tick) },
      zones: this.zones.map((z) => ({ ...z, closed: this.closedZones.has(z.id) })),
      robots,
      workers: this.workers.map((w) => ({ ...w })),
      docks: BASE.docks.map((d) => ({
        ...d,
        open: this.dockOpen.get(d.id) ?? true,
        queue: [...(this.dockQueue.get(d.id) ?? [])],
        delivered: this.dockDelivered.get(d.id) ?? 0,
      })),
      chargers: BASE.chargers.map((c) => ({
        ...c,
        enabled: this.chargerEnabled.get(c.id) ?? true,
        occupants: [...(this.chargerOccupants.get(c.id) ?? [])],
      })),
      conveyors: BASE.conveyors ?? [],
      orders: orders === "none" ? [] : open.slice(0, 400),
      tasks: this.tasks(),
      stats: {
        ...BASE.stats,
        orders_created: this.created,
        orders_delivered: this.delivered,
        orders_late: this.late,
        orders_cancelled: this.cancelled,
        fulfillment_ticks_total: this.fulfillmentTotal,
        lateness_ticks_total: this.latenessTotal,
        distance_total: this.distanceTotal,
        energy_total: this.energyTotal,
        congestion_ticks_total: this.congestionTotal,
        wait_ticks_total: this.waitTotal,
        replans_total: this.replans,
        failures_total: this.failures,
        charging_sessions: this.chargingSessions,
        ticks: this.tick,
        productive_robot_ticks: this.productiveTotal,
        operational_robot_ticks: this.operationalTotal,
      },
      demand: {
        ...BASE.demand,
        multiplier: this.demandMultiplier,
        burst_until_tick: this.burstUntil,
        burst_multiplier: this.burstMultiplier,
      },
      config: BASE.config,
      zone_occupancy: { ...this.zoneOccupancy },
      kpis: this.kpis(),
    };
    if (includeGrid) {
      snap.grid = {
        ...this.grid,
        blocked: [...this.blocked].map((k) => [k % 4096, Math.floor(k / 4096)] as Cell),
        closed_zones: [...this.closedZones].sort(),
      };
      snap.shelves = BASE.shelves;
    }
    return snap;
  }

  frame(): TickFrame {
    const k = this.kpis();
    return {
      type: "tick",
      tick: this.tick,
      sim_time: simTimeIso(this.tick),
      robots: [...this.robots.values()].map((r) => ({
        id: r.id,
        cell: [r.cell[0], r.cell[1]] as Cell,
        status: this.robotStatus(r),
        battery: Math.round(r.battery * 10) / 10,
        task_id: r.taskId,
        path: r.path.slice(0, 64).map((c) => [c[0], c[1]] as Cell),
        zone_id: r.zoneId,
        load: r.load,
      })),
      kpis: {
        sla_breach_rate_projected: k.sla_breach_rate_projected,
        sla_breach_rate: k.sla_breach_rate,
        avg_fulfillment_min: k.avg_fulfillment_min,
        throughput_per_hour: k.throughput_per_hour,
        robot_utilization: k.robot_utilization,
        congestion_index: k.congestion_index,
        orders_open: k.orders_open,
        orders_pending: k.orders_pending,
        orders_delivered: k.orders_delivered,
        orders_late: k.orders_late,
        orders_overdue_open: k.orders_overdue_open,
        robots_operational: k.robots_operational,
        robots_total: k.robots_total,
        failures: k.failures,
        sim_hours: k.sim_hours,
      },
      zone_occupancy: { ...this.zoneOccupancy },
      docks: BASE.docks.map((d) => ({
        id: d.id,
        queue: (this.dockQueue.get(d.id) ?? []).length,
        open: this.dockOpen.get(d.id) ?? true,
      })),
      chargers: BASE.chargers.map((c) => ({ id: c.id, occupants: [...(this.chargerOccupants.get(c.id) ?? [])] })),
    };
  }

  private takeSnapshot(): void {
    const k = this.kpis();
    this.snapshots.push({
      tick: this.tick,
      sim_time: simTimeIso(this.tick),
      digest: Math.abs(Math.imul(this.tick + 1, 2654435761) ^ this.seq).toString(16).padStart(8, "0"),
      kpis: {
        sla_breach_rate_projected: k.sla_breach_rate_projected,
        orders_open: k.orders_open,
        throughput_per_hour: k.throughput_per_hour,
        congestion_index: k.congestion_index,
      },
      size_bytes: 48_000 + this.robots.size * 900 + this.orders.size * 400,
      robots: this.robotModels(),
      zone_occupancy: { ...this.zoneOccupancy },
    });
    if (this.snapshots.length > 60) this.snapshots.shift();
  }

  snapshot(tick: number): WorldSnapshot | null {
    const rec = this.snapshots.find((s) => s.tick === tick);
    if (!rec) return null;
    const w = this.world("none", true);
    return {
      ...w,
      summary: { ...w.summary, tick: rec.tick, sim_time: rec.sim_time },
      clock: { tick: rec.tick, tick_seconds: 1, sim_time: rec.sim_time },
      robots: rec.robots,
      zone_occupancy: rec.zone_occupancy,
      orders: [],
      tasks: [],
    };
  }

  snapshotInfos(): SnapshotInfo[] {
    return this.snapshots.map(({ tick, sim_time, digest, kpis, size_bytes }) => ({ tick, sim_time, digest, kpis, size_bytes }));
  }
}

let instance: MockSim | null = null;

export function getSim(): MockSim {
  if (!instance) instance = new MockSim();
  return instance;
}
