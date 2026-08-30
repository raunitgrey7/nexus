import { API_URL } from "./env";
import type {
  BenchmarkResults,
  DecisionActionRequest,
  DecisionModel,
  DecisionRequest,
  EntityResponse,
  EventModel,
  FaultPreset,
  Forecast,
  HealthResponse,
  InjectEventRequest,
  KPIModel,
  NLQRequest,
  NLQResponse,
  OrderModel,
  RobotModel,
  SimControlRequest,
  SimStatus,
  SpatialResponse,
  StrategyInfo,
  TimelineResponse,
  WhatIfPreset,
  WhatIfRequest,
  WhatIfResult,
  WorldSnapshot,
} from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly path: string;

  constructor(status: number, detail: string, path: string) {
    super(`${status} ${detail}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.path = path;
  }
}

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.status === 0 ? e.detail : `${e.detail} (HTTP ${e.status})`;
  if (e instanceof Error) return e.message;
  return String(e);
}

type Query = Record<string, string | number | boolean | null | undefined>;

function qs(params?: Query): string {
  if (!params) return "";
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

interface RequestOptions {
  method?: "GET" | "POST" | "DELETE";
  body?: unknown;
  query?: Query;
  timeoutMs?: number;
}

async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), opts.timeoutMs ?? 30_000);
  const url = `${API_URL}${path}${qs(opts.query)}`;
  let res: Response;
  try {
    res = await fetch(url, {
      method: opts.method ?? "GET",
      headers: opts.body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: controller.signal,
      cache: "no-store",
    });
  } catch (e) {
    clearTimeout(timer);
    const aborted = e instanceof Error && e.name === "AbortError";
    throw new ApiError(0, aborted ? `Request timed out: ${path}` : `Backend unreachable at ${API_URL}`, path);
  }
  clearTimeout(timer);
  if (!res.ok) {
    let detail = res.statusText || `HTTP ${res.status}`;
    try {
      const data: unknown = await res.json();
      if (data && typeof data === "object" && "detail" in data) {
        const d = (data as { detail: unknown }).detail;
        detail = typeof d === "string" ? d : JSON.stringify(d);
      }
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail, path);
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  if (!text) return undefined as T;
  if (path === "/metrics") return text as T;
  return JSON.parse(text) as T;
}

export interface WorldQuery {
  orders?: "open" | "all" | "none";
  grid?: boolean;
}

export interface EventsQuery {
  since_seq?: number;
  limit?: number;
  types?: string[];
}

export interface RecentEventsQuery {
  limit?: number;
  notable?: boolean;
}

export interface TimelineQuery {
  from_tick?: number;
  to_tick?: number;
}

export interface OrdersQuery {
  status?: string;
  limit?: number;
}

/** Every REST endpoint of `docs/API.md`. Implemented by the HTTP client and by the mock. */
export interface NexusApi {
  health(): Promise<HealthResponse>;
  status(): Promise<SimStatus>;
  control(req: SimControlRequest): Promise<SimStatus>;
  world(q?: WorldQuery): Promise<WorldSnapshot>;
  robots(): Promise<RobotModel[]>;
  orders(q?: OrdersQuery): Promise<OrderModel[]>;
  entity(id: string): Promise<EntityResponse>;
  kpis(sinceTick?: number): Promise<KPIModel>;
  spatial(): Promise<SpatialResponse>;
  events(q?: EventsQuery): Promise<EventModel[]>;
  recentEvents(q?: RecentEventsQuery): Promise<EventModel[]>;
  injectEvent(req: InjectEventRequest): Promise<EventModel>;
  faultPresets(): Promise<FaultPreset[]>;
  fireFault(presetId: string): Promise<EventModel>;
  forecast(horizonMin?: number): Promise<Forecast>;
  createDecision(req: DecisionRequest): Promise<DecisionModel>;
  decisions(limit?: number): Promise<DecisionModel[]>;
  decision(id: string): Promise<DecisionModel>;
  decisionAction(id: string, req: DecisionActionRequest): Promise<DecisionModel>;
  createWhatIf(req: WhatIfRequest): Promise<WhatIfResult>;
  whatifs(): Promise<WhatIfResult[]>;
  whatif(id: string): Promise<WhatIfResult>;
  whatifPresets(): Promise<WhatIfPreset[]>;
  nlq(req: NLQRequest): Promise<NLQResponse>;
  timeline(q?: TimelineQuery): Promise<TimelineResponse>;
  snapshot(tick: number): Promise<WorldSnapshot>;
  benchmarks(): Promise<BenchmarkResults>;
  strategies(): Promise<StrategyInfo[]>;
}

export const restApi: NexusApi = {
  health: () => request("/api/health", { timeoutMs: 5_000 }),
  status: () => request("/api/status"),
  control: (req) => request("/api/sim/control", { method: "POST", body: req, timeoutMs: 60_000 }),
  world: (q) => request("/api/world", { query: { orders: q?.orders ?? "open", grid: q?.grid ?? true } }),
  robots: () => request("/api/world/robots"),
  orders: (q) => request("/api/world/orders", { query: { status: q?.status, limit: q?.limit } }),
  entity: (id) => request(`/api/world/entity/${encodeURIComponent(id)}`),
  kpis: (sinceTick) => request("/api/kpis", { query: { since_tick: sinceTick } }),
  spatial: () => request("/api/spatial"),
  events: (q) =>
    request("/api/events", {
      query: { since_seq: q?.since_seq, limit: q?.limit, types: q?.types?.join(",") },
    }),
  recentEvents: (q) => request("/api/events/recent", { query: { limit: q?.limit, notable: q?.notable } }),
  injectEvent: (req) => request("/api/events/inject", { method: "POST", body: req }),
  faultPresets: () => request("/api/faults/presets"),
  fireFault: (presetId) => request(`/api/faults/${encodeURIComponent(presetId)}`, { method: "POST" }),
  forecast: (horizonMin) => request("/api/forecast", { query: { horizon_min: horizonMin }, timeoutMs: 60_000 }),
  createDecision: (req) => request("/api/decisions", { method: "POST", body: req, timeoutMs: 300_000 }),
  decisions: (limit) => request("/api/decisions", { query: { limit } }),
  decision: (id) => request(`/api/decisions/${encodeURIComponent(id)}`),
  decisionAction: (id, req) =>
    request(`/api/decisions/${encodeURIComponent(id)}/actions`, { method: "POST", body: req, timeoutMs: 120_000 }),
  createWhatIf: (req) => request("/api/whatif", { method: "POST", body: req, timeoutMs: 60_000 }),
  whatifs: () => request("/api/whatif"),
  whatif: (id) => request(`/api/whatif/${encodeURIComponent(id)}`),
  whatifPresets: () => request("/api/whatif/presets"),
  nlq: (req) => request("/api/nlq", { method: "POST", body: req, timeoutMs: 180_000 }),
  timeline: (q) => request("/api/timeline", { query: { from_tick: q?.from_tick, to_tick: q?.to_tick } }),
  snapshot: (tick) => request(`/api/snapshots/${tick}`),
  benchmarks: () => request("/api/benchmarks"),
  strategies: () => request("/api/strategies"),
};
