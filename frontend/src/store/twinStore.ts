import { create } from "zustand";
import { api, createLiveClient } from "@/lib/client";
import { errorMessage } from "@/lib/api";
import { IS_MOCK } from "@/lib/env";
import type { LiveClient } from "@/lib/ws";
import type {
  ConnectionState,
  EventModel,
  FaultPreset,
  KPIModel,
  RobotModel,
  ServerFrame,
  SimControlRequest,
  SimStatus,
  StrategyInfo,
  TickFrame,
  WorldSnapshot,
} from "@/lib/types";
import { useDecisionStore } from "./decisionStore";
import { useForecastStore } from "./forecastStore";
import { useWhatIfStore } from "./whatifStore";

export const EVENT_RING = 300;

export type ViewMode = "3d" | "2d";
export type PanelTab = "events" | "robots" | "orders" | "zones";

const WORLD_REFRESH_EVENTS = new Set([
  "AISLE_BLOCKED",
  "AISLE_CLEARED",
  "ZONE_CLOSED",
  "ZONE_OPENED",
  "DOCK_CLOSED",
  "DOCK_OPENED",
  "CHARGER_DISABLED",
  "CHARGER_ENABLED",
  "ROBOT_ADDED",
  "ROBOT_REMOVED",
  "PLAN_EXECUTED",
]);

interface TwinState {
  connection: ConnectionState;
  booted: boolean;
  world: WorldSnapshot | null;
  worldLoading: boolean;
  worldError: string | null;
  robots: Record<string, RobotModel>;
  robotIds: string[];
  kpis: KPIModel | null;
  status: SimStatus | null;
  tick: number;
  simTime: string;
  tickSeconds: number;
  events: EventModel[];
  zoneOccupancy: Record<string, number>;
  dockQueues: Record<string, number>;
  dockOpen: Record<string, boolean>;
  chargerOccupants: Record<string, string[]>;
  faultPresets: FaultPreset[];
  strategies: StrategyInfo[];
  selectedRobotId: string | null;
  viewMode: ViewMode;
  panelTab: PanelTab;
  lastError: string | null;
  controlBusy: boolean;
  faultBusy: string | null;
  /** bumped to ask the 3D camera to re-fit the world */
  fitNonce: number;

  boot(): void;
  requestFit(): void;
  loadWorld(full?: boolean): Promise<void>;
  refreshStatus(): Promise<void>;
  control(req: SimControlRequest): Promise<SimStatus | null>;
  fireFault(presetId: string): Promise<EventModel | null>;
  applyFrame(frame: ServerFrame): void;
  pushEvent(ev: EventModel): void;
  selectRobot(id: string | null): void;
  setViewMode(m: ViewMode): void;
  setPanelTab(t: PanelTab): void;
  clearError(): void;
}

let live: LiveClient | null = null;
let refreshTimer: ReturnType<typeof setTimeout> | null = null;

function indexRobots(list: RobotModel[]): { robots: Record<string, RobotModel>; robotIds: string[] } {
  const robots: Record<string, RobotModel> = {};
  const robotIds: string[] = [];
  for (const r of list) {
    robots[r.id] = r;
    robotIds.push(r.id);
  }
  return { robots, robotIds };
}

function mergeTick(state: TwinState, frame: TickFrame): Partial<TwinState> {
  const robots = { ...state.robots };
  let robotIds = state.robotIds;
  for (const tr of frame.robots) {
    const prev = robots[tr.id];
    if (prev) {
      robots[tr.id] = {
        ...prev,
        cell: tr.cell,
        status: tr.status,
        battery: tr.battery,
        task_id: tr.task_id,
        path: tr.path ?? [],
        zone_id: tr.zone_id ?? prev.zone_id,
        load: tr.load ?? prev.load,
      };
    } else {
      robots[tr.id] = {
        id: tr.id,
        cell: tr.cell,
        zone_id: tr.zone_id ?? "",
        battery: tr.battery,
        status: tr.status,
        task_id: tr.task_id,
        path: tr.path ?? [],
        speed: 1,
        capacity: 10,
        load: tr.load ?? 0,
        action_until_tick: 0,
        wait_ticks: 0,
        distance: 0,
        energy: 0,
        productive_ticks: 0,
        operational_ticks: 0,
        failure_cause: null,
        failed_tick: null,
        recover_at_tick: null,
        charger_id: null,
        tasks_completed: 0,
        orders_completed: 0,
      };
      if (!robotIds.includes(tr.id)) robotIds = [...robotIds, tr.id];
    }
  }
  const dockQueues = { ...state.dockQueues };
  const dockOpen = { ...state.dockOpen };
  for (const d of frame.docks ?? []) {
    dockQueues[d.id] = Array.isArray(d.queue) ? d.queue.length : d.queue;
    dockOpen[d.id] = d.open;
  }
  const chargerOccupants = { ...state.chargerOccupants };
  for (const c of frame.chargers ?? []) chargerOccupants[c.id] = c.occupants;

  const kpis = state.kpis ? { ...state.kpis, ...frame.kpis, tick: frame.tick } : state.kpis;
  const status = state.status ? { ...state.status, tick: frame.tick, sim_time: frame.sim_time } : state.status;

  // dynamic walkability (aisle blockages / zone closures) piggybacks on tick frames; only rebuild the
  // world object when it actually changed so the 3D static layers are not re-created 20×/s
  let world = state.world;
  const grid = world?.grid;
  if (world && grid && (frame.blocked || frame.closed_zones)) {
    const blocked = frame.blocked ?? grid.blocked;
    const closed = frame.closed_zones ?? grid.closed_zones;
    const sameBlocked =
      blocked.length === grid.blocked.length && blocked.every((c, i) => c[0] === grid.blocked[i]?.[0] && c[1] === grid.blocked[i]?.[1]);
    const sameClosed = closed.length === grid.closed_zones.length && closed.every((z, i) => z === grid.closed_zones[i]);
    if (!sameBlocked || !sameClosed) {
      const closedSet = new Set(closed);
      world = {
        ...world,
        grid: { ...grid, blocked, closed_zones: closed },
        zones: world.zones.map((z) => (z.closed === closedSet.has(z.id) ? z : { ...z, closed: closedSet.has(z.id) })),
      };
    }
  }
  return {
    robots,
    robotIds,
    world,
    tick: frame.tick,
    simTime: frame.sim_time,
    kpis,
    status,
    zoneOccupancy: frame.zone_occupancy ?? state.zoneOccupancy,
    dockQueues,
    dockOpen,
    chargerOccupants,
  };
}

export const useTwinStore = create<TwinState>((set, get) => ({
  connection: "idle",
  booted: false,
  world: null,
  worldLoading: false,
  worldError: null,
  robots: {},
  robotIds: [],
  kpis: null,
  status: null,
  tick: 0,
  simTime: "",
  tickSeconds: 1,
  events: [],
  zoneOccupancy: {},
  dockQueues: {},
  dockOpen: {},
  chargerOccupants: {},
  faultPresets: [],
  strategies: [],
  selectedRobotId: null,
  viewMode: "3d",
  panelTab: "events",
  lastError: null,
  controlBusy: false,
  faultBusy: null,
  fitNonce: 0,

  requestFit() {
    set((s) => ({ fitNonce: s.fitNonce + 1 }));
  },

  boot() {
    if (get().booted || typeof window === "undefined") return;
    set({ booted: true });
    void get().loadWorld(true);
    void api
      .faultPresets()
      .then((faultPresets) => set({ faultPresets }))
      .catch(() => undefined);
    void api
      .strategies()
      .then((strategies) => set({ strategies }))
      .catch(() => undefined);
    void api
      .recentEvents({ limit: 100 })
      .then((events) => {
        if (get().events.length === 0) set({ events: events.slice(-EVENT_RING) });
      })
      .catch(() => undefined);
    void createLiveClient().then((client) => {
      live = client;
      client.onState((connection) => set({ connection: IS_MOCK ? "mock" : connection }));
      client.onFrame((frame) => get().applyFrame(frame));
      client.connect();
    });
  },

  async loadWorld(full = true) {
    set({ worldLoading: !get().world, worldError: null });
    try {
      const world = await api.world({ orders: "open", grid: full });
      const prev = get().world;
      const merged: WorldSnapshot =
        !full && prev ? { ...world, grid: prev.grid, shelves: prev.shelves } : world;
      const idx = indexRobots(world.robots);
      set({
        world: merged,
        worldLoading: false,
        robots: idx.robots,
        robotIds: idx.robotIds,
        kpis: world.kpis ?? get().kpis,
        tick: world.clock.tick,
        simTime: world.clock.sim_time,
        tickSeconds: world.clock.tick_seconds,
        zoneOccupancy: world.zone_occupancy,
        dockQueues: Object.fromEntries(world.docks.map((d) => [d.id, d.queue.length])),
        dockOpen: Object.fromEntries(world.docks.map((d) => [d.id, d.open])),
        chargerOccupants: Object.fromEntries(world.chargers.map((c) => [c.id, c.occupants])),
      });
      if (!get().status) void get().refreshStatus();
      if (!world.kpis && !get().kpis) {
        void api
          .kpis()
          .then((kpis) => set({ kpis }))
          .catch(() => undefined);
      }
    } catch (e) {
      set({ worldLoading: false, worldError: errorMessage(e) });
    }
  },

  async refreshStatus() {
    try {
      const status = await api.status();
      set({ status, tick: status.tick, simTime: status.sim_time });
    } catch {
      /* status is optional until the socket says hello */
    }
  },

  async control(req) {
    set({ controlBusy: true, lastError: null });
    try {
      const status = await api.control(req);
      set({ status, controlBusy: false, tick: status.tick, simTime: status.sim_time });
      if (req.action === "reset") {
        set({ events: [], selectedRobotId: null });
        await get().loadWorld(true);
      }
      return status;
    } catch (e) {
      set({ controlBusy: false, lastError: errorMessage(e) });
      return null;
    }
  },

  async fireFault(presetId) {
    set({ faultBusy: presetId, lastError: null });
    try {
      const ev = await api.fireFault(presetId);
      get().pushEvent(ev);
      set({ faultBusy: null });
      return ev;
    } catch (e) {
      set({ faultBusy: null, lastError: errorMessage(e) });
      return null;
    }
  },

  applyFrame(frame) {
    switch (frame.type) {
      case "hello": {
        const idx = indexRobots(frame.world.robots);
        const prev = get().world;
        const world: WorldSnapshot = frame.world.grid
          ? frame.world
          : { ...frame.world, grid: prev?.grid, shelves: prev?.shelves };
        set({
          world,
          worldLoading: false,
          worldError: null,
          robots: idx.robots,
          robotIds: idx.robotIds,
          kpis: frame.kpis,
          status: frame.status,
          tick: frame.world.clock.tick,
          simTime: frame.world.clock.sim_time,
          tickSeconds: frame.world.clock.tick_seconds,
          zoneOccupancy: frame.world.zone_occupancy,
          dockQueues: Object.fromEntries(frame.world.docks.map((d) => [d.id, d.queue.length])),
          dockOpen: Object.fromEntries(frame.world.docks.map((d) => [d.id, d.open])),
          chargerOccupants: Object.fromEntries(frame.world.chargers.map((c) => [c.id, c.occupants])),
        });
        if (!world.grid) void get().loadWorld(true);
        break;
      }
      case "tick":
        set((s) => mergeTick(s, frame));
        break;
      case "event": {
        get().pushEvent(frame.event);
        if (WORLD_REFRESH_EVENTS.has(frame.event.type)) {
          if (refreshTimer) clearTimeout(refreshTimer);
          refreshTimer = setTimeout(() => void get().loadWorld(true), 400);
        }
        break;
      }
      case "decision":
        useDecisionStore.getState().upsert(frame.decision);
        break;
      case "forecast":
        useForecastStore.getState().setForecast(frame.forecast);
        break;
      case "whatif":
        useWhatIfStore.getState().upsert(frame.result);
        break;
      case "status":
        set({ status: frame.status, tick: frame.status.tick, simTime: frame.status.sim_time });
        break;
      case "pong":
        break;
    }
  },

  pushEvent(ev) {
    set((s) => {
      if (s.events.some((e) => e.id === ev.id && e.seq === ev.seq)) return {};
      const events = s.events.length >= EVENT_RING ? [...s.events.slice(-(EVENT_RING - 1)), ev] : [...s.events, ev];
      return { events };
    });
  },

  selectRobot(id) {
    set({ selectedRobotId: id });
  },

  setViewMode(viewMode) {
    set({ viewMode });
  },

  setPanelTab(panelTab) {
    set({ panelTab });
  },

  clearError() {
    set({ lastError: null });
  },
}));

export function sendControl(frame: Parameters<LiveClient["send"]>[0]): void {
  live?.send(frame);
}
