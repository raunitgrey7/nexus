/**
 * TypeScript mirrors of `backend/nexus/api/schemas.py` and of the world snapshot produced by
 * `WorldState.to_dict()`. Keep field names identical to the wire format (snake_case).
 */

// ------------------------------------------------------------------------------------------------
// primitives
// ------------------------------------------------------------------------------------------------

/** `[x, y]` integer pair; origin bottom-left, y grows "north". */
export type Cell = [number, number];

/** `dict[str, Any]` on the Python side. */
export type Dict = Record<string, unknown>;

export type RiskLevel = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
export type Severity = "info" | "low" | "medium" | "high" | "critical";

// ------------------------------------------------------------------------------------------------
// simulation control / status
// ------------------------------------------------------------------------------------------------

export type SimControlAction = "start" | "pause" | "step" | "reset" | "speed";

export interface SimControlRequest {
  action: SimControlAction;
  ticks?: number;
  ticks_per_second?: number | null;
  scale?: string | null;
  seed?: number | null;
  strategy?: string | null;
  autopilot?: boolean | null;
}

export interface LLMStatus {
  enabled: boolean;
  model: string;
  available: boolean;
  url: string;
}

export interface SimStatus {
  running: boolean;
  tick: number;
  sim_time: string;
  ticks_per_second: number;
  strategy: string;
  scale: string;
  seed: number;
  domain: string;
  autopilot: boolean;
  events_persisted: number;
  decisions: number;
  llm: LLMStatus;
  uptime_s: number;
}

export interface HealthResponse {
  status: string;
  version: string;
  tick: number;
  llm: LLMStatus | Dict;
}

export interface StrategyInfo {
  name: string;
  description: string;
}

// ------------------------------------------------------------------------------------------------
// events
// ------------------------------------------------------------------------------------------------

export interface InjectEventRequest {
  type: string;
  entity_id?: string | null;
  payload?: Dict;
  key?: string | null;
}

export interface EventModel {
  id: string;
  seq: number;
  type: string;
  tick: number;
  entity_id: string | null;
  payload: Dict;
  origin: string;
  cause?: string | null;
  ephemeral?: boolean;
}

export interface FaultPreset {
  id: string;
  name: string;
  description: string;
  event: InjectEventRequest;
}

// ------------------------------------------------------------------------------------------------
// KPIs
// ------------------------------------------------------------------------------------------------

export interface KPIModel {
  tick: number;
  sim_hours: number;
  orders_created: number;
  orders_delivered: number;
  orders_open: number;
  orders_pending: number;
  orders_late: number;
  orders_overdue_open: number;
  orders_cancelled: number;
  avg_fulfillment_min: number;
  p50_fulfillment_min: number;
  p95_fulfillment_min: number;
  sla_breach_rate: number;
  sla_breach_rate_projected: number;
  throughput_per_hour: number;
  robot_utilization: number;
  robot_availability: number;
  robots_total: number;
  robots_operational: number;
  distance_total: number;
  energy_total: number;
  congestion_index: number;
  wait_ticks_per_robot_hour: number;
  replans: number;
  failures: number;
  charging_sessions: number;
  inventory_units: number;
  avg_lateness_min: number;
}

export interface TimelinePoint {
  tick: number;
  open: number;
  delivered: number;
  breach_projected: number;
  congestion: number;
  utilization?: number;
}

// ------------------------------------------------------------------------------------------------
// forecasting
// ------------------------------------------------------------------------------------------------

export interface DemandBucket {
  start_min: number;
  end_min: number;
  expected_orders: number;
  lower: number;
  upper: number;
}

export type Trend = "rising" | "flat" | "falling";

export interface DemandForecast {
  horizon_min: number;
  expected_orders: number;
  per_bucket: DemandBucket[];
  trend: Trend;
  current_rate_per_hour: number;
  forecast_rate_per_hour: number;
  capacity_per_hour: number;
  projected_utilization: number;
  confidence: number;
  method: string;
}

export type LowMedHigh = "low" | "medium" | "high";

export interface BatteryForecast {
  robot_id: string;
  battery: number;
  status: string;
  workload_tasks: number;
  predicted_exhaustion_min: number | null;
  charger_eta_min: number | null;
  risk: LowMedHigh;
  recommendation: string;
}

export interface CongestionForecast {
  zone_id: string;
  zone_name: string;
  robots_now: number;
  capacity: number;
  projected_robots: number;
  projected_change_pct: number;
  eta_min: number;
  risk: LowMedHigh;
  drivers: string[];
}

export type BottleneckKind = "zone" | "dock" | "charger" | "robot" | "inventory" | "worker" | "demand";

export interface Bottleneck {
  kind: BottleneckKind;
  entity_id: string;
  severity: number;
  message: string;
  recommendation: string;
}

export interface Forecast {
  generated_tick: number;
  sim_time: string;
  demand: DemandForecast;
  battery: BatteryForecast[];
  congestion: CongestionForecast[];
  bottlenecks: Bottleneck[];
  summary: string;
}

// ------------------------------------------------------------------------------------------------
// plans / decisions
// ------------------------------------------------------------------------------------------------

export type ActionType =
  | "REASSIGN_TASKS"
  | "REPRIORITIZE_ORDERS"
  | "SEND_TO_CHARGE"
  | "REROUTE_AVOID_ZONE"
  | "PREFER_CORRIDOR"
  | "REPOSITION_INVENTORY"
  | "SET_BATCHING"
  | "SET_ZONE_CAPACITY"
  | "CLOSE_ZONE"
  | "OPEN_ZONE"
  | "ADD_ROBOTS"
  | "REMOVE_ROBOTS"
  | "DISPATCH_WORKER"
  | "CANCEL_TASKS"
  | "SET_STRATEGY"
  | "NOOP";

export interface ActionModel {
  type: ActionType;
  params: Dict;
  rationale: string;
}

export interface SimulationOutcome {
  horizon_ticks: number;
  kpis: KPIModel;
  delta_vs_baseline: Record<string, number>;
  score: number;
  timeline: TimelinePoint[];
  duration_ms: number;
  /** present on the live backend; free-form */
  diagnostics?: Dict;
  events_applied?: number | unknown[];
}

export type RiskFindingKind =
  | "deadlock"
  | "safety"
  | "resource_exhaustion"
  | "instability"
  | "constraint"
  | "regression"
  | "capacity";

export interface RiskFinding {
  kind: RiskFindingKind;
  severity: Severity;
  message: string;
  entity_ids: string[];
}

export interface RiskReport {
  level: RiskLevel;
  score: number;
  findings: RiskFinding[];
  stability: Record<string, number>;
  checked_seeds: number;
}

export type PlanSource = "llm" | "heuristic" | "optimizer" | "user";

export interface PlanModel {
  id: string;
  name: string;
  source: PlanSource;
  description: string;
  actions: ActionModel[];
  optimized: boolean;
  feasible: boolean;
  validation_errors: string[];
  simulation: SimulationOutcome | null;
  risk: RiskReport | null;
  rank: number | null;
}

export interface ApprovalModel {
  policy: "auto" | "human";
  auto_approved: boolean;
  reason: string;
  approved_by: string | null;
  approved_tick: number | null;
}

export type DecisionStatus = "proposed" | "approved" | "rejected" | "executed" | "failed";

export interface DecisionModel {
  id: string;
  created_tick: number;
  sim_time: string;
  trigger: string;
  goal: string;
  status: DecisionStatus;
  situation: Dict;
  baseline: SimulationOutcome | null;
  candidates: PlanModel[];
  recommended_plan_id: string | null;
  approval: ApprovalModel;
  explanation: string;
  timings: Record<string, number>;
  candidates_evaluated: number;
  llm_used: boolean;
  llm_model: string | null;
}

export interface DecisionRequest {
  goal?: string;
  trigger?: string;
  horizon_min?: number;
  candidates?: number | null;
  context?: Dict;
  use_llm?: boolean | null;
}

export type DecisionAction = "approve" | "reject" | "execute";

export interface DecisionActionRequest {
  action: DecisionAction;
  plan_id?: string | null;
  note?: string;
  actor?: string;
}

// ------------------------------------------------------------------------------------------------
// what-if
// ------------------------------------------------------------------------------------------------

export type MutationType =
  | "ROBOT_FAILURE"
  | "REMOVE_ROBOTS"
  | "ADD_ROBOTS"
  | "DEMAND_MULTIPLIER"
  | "DEMAND_BURST"
  | "CLOSE_ZONE"
  | "CLOSE_DOCK"
  | "DISABLE_CHARGERS"
  | "BLOCK_AISLE"
  | "MOVE_INVENTORY"
  | "WORKER_DELAY"
  | "SET_SLA"
  | "SET_BATCHING";

export interface MutationModel {
  type: MutationType;
  params: Dict;
  at_min: number;
}

export interface ScenarioModel {
  name: string;
  description: string;
  mutations: MutationModel[];
}

export interface WhatIfRequest {
  scenario: ScenarioModel;
  strategies: string[];
  horizon_min: number;
  seeds: number;
  include_current: boolean;
}

export interface WhatIfRun {
  strategy: string;
  label: string;
  seed: number;
  kpis: KPIModel;
  delta_vs_reference: Record<string, number>;
  timeline: TimelinePoint[];
  duration_ms: number;
}

export type WhatIfStatus = "queued" | "running" | "done" | "failed";

export interface WhatIfResult {
  id: string;
  status: WhatIfStatus;
  scenario: ScenarioModel;
  created_tick: number;
  horizon_ticks: number;
  reference: WhatIfRun | null;
  runs: WhatIfRun[];
  best_strategy: string | null;
  narrative: string;
  comparison: Dict[];
  error: string | null;
}

export interface WhatIfPreset {
  id: string;
  name: string;
  question: string;
  description: string;
  scenario: ScenarioModel;
}

// ------------------------------------------------------------------------------------------------
// natural language
// ------------------------------------------------------------------------------------------------

export interface NLQRequest {
  question: string;
  horizon_min?: number;
  use_llm?: boolean | null;
}

export type NLQIntent = "explain" | "whatif" | "status" | "forecast" | "recommend" | "entity" | "unknown";

export interface NLQResponse {
  answer: string;
  intent: NLQIntent;
  data: Dict & { whatif?: WhatIfResult };
  llm_used: boolean;
  model: string | null;
  latency_ms: number;
  suggestions: string[];
}

// ------------------------------------------------------------------------------------------------
// spatial
// ------------------------------------------------------------------------------------------------

export interface SpatialNode extends Dict {
  id: string;
  kind?: string;
}

export interface SpatialEdge {
  source: string;
  target: string;
  rel: string;
}

export interface SpatialResponse {
  nodes: SpatialNode[];
  edges: SpatialEdge[];
  zone_load: Record<string, Record<string, number>>;
  zone_adjacency: Record<string, string[]>;
}

export interface EntityRelations {
  entity_id: string;
  kind: string | null;
  triples: string[][];
  description: string[];
}

// ------------------------------------------------------------------------------------------------
// snapshots / timeline
// ------------------------------------------------------------------------------------------------

export interface SnapshotInfo {
  tick: number;
  sim_time: string;
  digest: string;
  kpis: Record<string, number>;
  size_bytes: number;
}

export interface TimelineResponse {
  points: TimelinePoint[];
  snapshots: SnapshotInfo[];
  notable_events: EventModel[];
}

export interface OkResponse {
  ok: boolean;
  message: string;
  data: Dict;
}

// ------------------------------------------------------------------------------------------------
// world snapshot (WorldState.to_dict)
// ------------------------------------------------------------------------------------------------

/** grid.rows[y][x] digit. */
export const CELL = {
  FLOOR: "0",
  SHELF: "1",
  WALL: "2",
  DOCK: "3",
  CHARGER: "4",
  CONVEYOR: "5",
  STAGING: "6",
} as const;
export type CellTypeDigit = (typeof CELL)[keyof typeof CELL];

export type ZoneKind = "storage" | "corridor" | "dock" | "charging" | "staging";

export type RobotStatus =
  | "idle"
  | "moving"
  | "picking"
  | "delivering"
  | "unloading"
  | "to_charger"
  | "charging"
  | "waiting"
  | "failed"
  | "maintenance";

export const ROBOT_STATUSES: RobotStatus[] = [
  "idle",
  "moving",
  "picking",
  "delivering",
  "unloading",
  "to_charger",
  "charging",
  "waiting",
  "failed",
  "maintenance",
];

export type OrderStatus = "pending" | "assigned" | "in_progress" | "delivered" | "cancelled";
export type OrderPriorityName = "LOW" | "NORMAL" | "HIGH" | "CRITICAL";
export type TaskStatus = "planned" | "active" | "completed" | "cancelled";
export type WorkerStatus = "available" | "busy" | "break" | "absent" | "delayed";
export type WaypointKind = "pick" | "deliver" | "charge" | "move";

export interface ZoneModel {
  id: string;
  name: string;
  kind: ZoneKind;
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  capacity: number;
  closed: boolean;
}

export interface ShelfModel {
  id: string;
  cell: Cell;
  access_cell: Cell;
  zone_id: string;
  inventory: Record<string, number>;
}

export interface ChargerModel {
  id: string;
  cell: Cell;
  zone_id: string;
  slots: number;
  enabled: boolean;
  occupants: string[];
}

export interface DockModel {
  id: string;
  cell: Cell;
  zone_id: string;
  open: boolean;
  queue: string[];
  delivered: number;
}

export interface ConveyorModel {
  id: string;
  cells: Cell[];
  zone_id: string;
  active: boolean;
}

export interface WorkerModel {
  id: string;
  name: string;
  role: string;
  cell: Cell;
  zone_id: string;
  status: WorkerStatus;
  delay_until_tick: number;
  orders_handled: number;
}

export interface OrderLineModel {
  sku: string;
  qty: number;
  shelf_id: string;
  picked: boolean;
}

export interface OrderModel {
  id: string;
  created_tick: number;
  deadline_tick: number;
  priority: number;
  priority_name: OrderPriorityName;
  lines: OrderLineModel[];
  status: OrderStatus;
  task_id: string | null;
  robot_id: string | null;
  dock_id: string | null;
  started_tick: number | null;
  delivered_tick: number | null;
  cancelled_tick: number | null;
  items: number;
}

export interface WaypointModel {
  kind: WaypointKind;
  target_id: string;
  cell: Cell;
  order_id: string | null;
  done: boolean;
}

export interface TaskModel {
  id: string;
  robot_id: string;
  order_ids: string[];
  waypoints: WaypointModel[];
  created_tick: number;
  status: TaskStatus;
  leg: number;
  completed_tick: number | null;
  origin: string;
}

export interface RobotModel {
  id: string;
  cell: Cell;
  zone_id: string;
  battery: number;
  status: RobotStatus;
  task_id: string | null;
  path: Cell[];
  speed: number;
  capacity: number;
  load: number;
  action_until_tick: number;
  wait_ticks: number;
  distance: number;
  energy: number;
  productive_ticks: number;
  operational_ticks: number;
  failure_cause: string | null;
  failed_tick: number | null;
  recover_at_tick: number | null;
  charger_id: string | null;
  tasks_completed: number;
  orders_completed: number;
}

export interface DemandProfileModel {
  orders_per_hour: number;
  hourly_multipliers: number[];
  multiplier: number;
  max_lines: number;
  max_qty: number;
  priority_weights: number[];
  burst_until_tick: number;
  burst_multiplier: number;
}

export interface SimConfigModel {
  tick_seconds: number;
  robot_speed: number;
  congestion_speed_factor: number;
  cell_capacity: number;
  corridor_cell_capacity: number;
  battery_drain_move: number;
  battery_drain_idle: number;
  battery_drain_action: number;
  battery_charge_rate: number;
  battery_low_threshold: number;
  battery_charge_target: number;
  battery_reserve_factor: number;
  pick_ticks: number;
  unload_ticks: number;
  sla_minutes: Record<OrderPriorityName, number>;
  max_wait_before_replan: number;
  unreachable_cancel_ticks: number;
  unload_no_loader_factor: number;
  robot_failure_rate_per_hour: number;
  failure_recovery_minutes: number;
  replenish_every_ticks: number;
  replenish_threshold: number;
  replenish_target: number;
  batch_max_orders: number;
  task_rebalance: boolean;
}

export interface RunningStatsModel {
  orders_created: number;
  orders_delivered: number;
  orders_late: number;
  orders_cancelled: number;
  fulfillment_ticks_total: number;
  lateness_ticks_total: number;
  distance_total: number;
  energy_total: number;
  picks_total: number;
  congestion_ticks_total: number;
  wait_ticks_total: number;
  replans_total: number;
  failures_total: number;
  charging_sessions: number;
  ticks: number;
  productive_robot_ticks: number;
  operational_robot_ticks: number;
}

export interface WorldSummary {
  name: string;
  domain: string;
  scale: string;
  seed: number;
  tick: number;
  sim_time: string;
  version: number;
  is_fork: boolean;
  robots_total: number;
  robots_operational: number;
  robots_failed: number;
  robots_charging: number;
  workers: number;
  zones: number;
  shelves: number;
  inventory_units: number;
  orders_open: number;
  orders_pending: number;
  orders_delivered: number;
  orders_late: number;
  tasks_active: number;
  congestion: number;
  blocked_cells: number;
  closed_zones: string[];
  labels: Record<string, string>;
}

export interface ClockModel {
  tick: number;
  tick_seconds: number;
  sim_time: string;
}

export interface GridModel {
  width: number;
  height: number;
  rows: string[];
  blocked: Cell[];
  closed_zones: string[];
}

export interface WorldSnapshot {
  summary: WorldSummary;
  clock: ClockModel;
  zones: ZoneModel[];
  robots: RobotModel[];
  workers: WorkerModel[];
  docks: DockModel[];
  chargers: ChargerModel[];
  conveyors: ConveyorModel[];
  orders: OrderModel[];
  tasks: TaskModel[];
  stats: RunningStatsModel;
  demand: DemandProfileModel;
  config: SimConfigModel;
  zone_occupancy: Record<string, number>;
  grid?: GridModel;
  shelves?: ShelfModel[];
  /** `GET /api/world` appends the current KPIs and the live strategy name. */
  kpis?: KPIModel;
  strategy?: string;
}

export interface EntityResponse extends Dict {
  relations?: EntityRelations;
}

// ------------------------------------------------------------------------------------------------
// benchmarks (backend/benchmarks/results/latest.json) — rendered defensively
// ------------------------------------------------------------------------------------------------

export interface BenchmarkStrategyResult {
  kpis_mean?: Record<string, number>;
  kpis_std?: Record<string, number>;
  runs?: number | unknown[];
  [k: string]: unknown;
}

export interface BenchmarkScale {
  strategies?: Record<string, BenchmarkStrategyResult>;
  [k: string]: unknown;
}

export interface BenchmarkResults {
  generated_at?: string;
  scales?: Record<string, BenchmarkScale>;
  summary_table?: Array<Record<string, unknown>>;
  [k: string]: unknown;
}

// ------------------------------------------------------------------------------------------------
// WebSocket protocol (`/ws/live`)
// ------------------------------------------------------------------------------------------------

export interface TickRobot {
  id: string;
  cell: Cell;
  status: RobotStatus;
  battery: number;
  task_id: string | null;
  path: Cell[];
  zone_id?: string;
  load?: number;
}

export interface TickDock {
  id: string;
  queue: number | string[];
  open: boolean;
}

export interface TickCharger {
  id: string;
  occupants: string[];
  enabled?: boolean;
}

export interface HelloFrame {
  type: "hello";
  world: WorldSnapshot;
  kpis: KPIModel;
  status: SimStatus;
}

export interface TickFrame {
  type: "tick";
  tick: number;
  sim_time: string;
  robots: TickRobot[];
  kpis: Partial<KPIModel>;
  zone_occupancy: Record<string, number>;
  docks?: TickDock[];
  chargers?: TickCharger[];
  /** sent by the live backend in addition to API.md: dynamic walkability + instantaneous congestion */
  blocked?: Cell[];
  closed_zones?: string[];
  congestion?: number;
}

export interface EventFrame {
  type: "event";
  event: EventModel;
}

export interface DecisionFrame {
  type: "decision";
  decision: DecisionModel;
}

export interface ForecastFrame {
  type: "forecast";
  forecast: Forecast;
}

export interface WhatIfFrame {
  type: "whatif";
  result: WhatIfResult;
}

export interface StatusFrame {
  type: "status";
  status: SimStatus;
}

export interface PongFrame {
  type: "pong";
}

export type ServerFrame =
  | HelloFrame
  | TickFrame
  | EventFrame
  | DecisionFrame
  | ForecastFrame
  | WhatIfFrame
  | StatusFrame
  | PongFrame;

export interface ControlFrame {
  type: "control";
  action: "start" | "pause" | "step" | "speed";
  ticks_per_second?: number;
  ticks?: number;
}

export interface SubscribeFrame {
  type: "subscribe";
  tick_every: number;
}

export interface PingFrame {
  type: "ping";
}

export type ClientFrame = ControlFrame | SubscribeFrame | PingFrame;

export type ConnectionState = "idle" | "connecting" | "open" | "reconnecting" | "closed" | "mock";
