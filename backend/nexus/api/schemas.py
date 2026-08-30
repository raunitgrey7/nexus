"""API contract (pydantic models).

These models are the boundary between the engine and the outside world (REST, WebSocket, UI,
LLM structured output). The engine's dataclasses never leak past this module. See ``docs/API.md``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

RiskLevel = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
Severity = Literal["info", "low", "medium", "high", "critical"]


# ------------------------------------------------------------------------------------------------
# simulation control / status
# ------------------------------------------------------------------------------------------------


class SimControlRequest(BaseModel):
    action: Literal["start", "pause", "step", "reset", "speed"]
    ticks: int = Field(default=1, ge=1, le=100_000, description="ticks to advance for `step`")
    ticks_per_second: float | None = Field(default=None, gt=0, le=10_000)
    scale: str | None = None
    seed: int | None = None
    strategy: str | None = None
    autopilot: bool | None = Field(default=None, description="let the Ops Manager agent act automatically")


class LLMStatus(BaseModel):
    enabled: bool
    model: str
    available: bool
    url: str


class SimStatus(BaseModel):
    running: bool
    tick: int
    sim_time: str
    ticks_per_second: float
    strategy: str
    scale: str
    seed: int
    domain: str
    autopilot: bool
    events_persisted: int
    decisions: int
    llm: LLMStatus
    uptime_s: float


# ------------------------------------------------------------------------------------------------
# events
# ------------------------------------------------------------------------------------------------


class InjectEventRequest(BaseModel):
    type: str = Field(description="EventType name, e.g. ROBOT_FAILURE")
    entity_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    key: str | None = Field(default=None, description="idempotency key")


class EventModel(BaseModel):
    id: str
    seq: int
    type: str
    tick: int
    entity_id: str | None
    payload: dict[str, Any]
    origin: str
    cause: str | None = None
    ephemeral: bool = False


class FaultPreset(BaseModel):
    id: str
    name: str
    description: str
    event: InjectEventRequest


# ------------------------------------------------------------------------------------------------
# KPIs
# ------------------------------------------------------------------------------------------------


class KPIModel(BaseModel):
    tick: int
    sim_hours: float
    orders_created: int
    orders_delivered: int
    orders_open: int
    orders_pending: int
    orders_late: int
    orders_overdue_open: int
    orders_cancelled: int
    avg_fulfillment_min: float
    p50_fulfillment_min: float
    p95_fulfillment_min: float
    sla_breach_rate: float
    sla_breach_rate_projected: float
    throughput_per_hour: float
    robot_utilization: float
    robot_availability: float
    robots_total: int
    robots_operational: int
    distance_total: int
    energy_total: float
    congestion_index: float
    wait_ticks_per_robot_hour: float
    replans: int
    failures: int
    charging_sessions: int
    inventory_units: int
    avg_lateness_min: float


class TimelinePoint(BaseModel):
    tick: int
    open: int
    delivered: int
    breach_projected: float
    congestion: float
    utilization: float = 0.0


# ------------------------------------------------------------------------------------------------
# forecasting
# ------------------------------------------------------------------------------------------------


class DemandBucket(BaseModel):
    start_min: int
    end_min: int
    expected_orders: float
    lower: float
    upper: float


class DemandForecast(BaseModel):
    horizon_min: int
    expected_orders: float
    per_bucket: list[DemandBucket]
    trend: Literal["rising", "flat", "falling"]
    current_rate_per_hour: float
    forecast_rate_per_hour: float
    capacity_per_hour: float
    projected_utilization: float
    confidence: float
    method: str


class BatteryForecast(BaseModel):
    robot_id: str
    battery: float
    status: str
    workload_tasks: int
    predicted_exhaustion_min: float | None
    charger_eta_min: float | None
    risk: Literal["low", "medium", "high"]
    recommendation: str


class CongestionForecast(BaseModel):
    zone_id: str
    zone_name: str
    robots_now: int
    capacity: int
    projected_robots: float
    projected_change_pct: float
    eta_min: float
    risk: Literal["low", "medium", "high"]
    drivers: list[str]


class Bottleneck(BaseModel):
    kind: Literal["zone", "dock", "charger", "robot", "inventory", "worker", "demand"]
    entity_id: str
    severity: float = Field(ge=0, le=1)
    message: str
    recommendation: str


class Forecast(BaseModel):
    generated_tick: int
    sim_time: str
    demand: DemandForecast
    battery: list[BatteryForecast]
    congestion: list[CongestionForecast]
    bottlenecks: list[Bottleneck]
    summary: str


# ------------------------------------------------------------------------------------------------
# plans / decisions
# ------------------------------------------------------------------------------------------------

ActionType = Literal[
    "REASSIGN_TASKS",  # move tasks from robots / zones to other robots
    "REPRIORITIZE_ORDERS",  # boost priority of a class of orders
    "SEND_TO_CHARGE",  # pre-emptive charging of specific robots
    "REROUTE_AVOID_ZONE",  # steer routing away from a zone / corridor
    "PREFER_CORRIDOR",  # bias routing towards a corridor
    "REPOSITION_INVENTORY",  # move stock of hot SKUs to another zone
    "SET_BATCHING",  # orders per trip
    "SET_ZONE_CAPACITY",  # soft capacity used by congestion-aware routing
    "CLOSE_ZONE",
    "OPEN_ZONE",
    "ADD_ROBOTS",
    "REMOVE_ROBOTS",
    "DISPATCH_WORKER",
    "CANCEL_TASKS",
    "SET_STRATEGY",
    "NOOP",
]


class ActionModel(BaseModel):
    type: ActionType
    params: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""


class SimulationOutcome(BaseModel):
    horizon_ticks: int
    kpis: KPIModel
    delta_vs_baseline: dict[str, float] = Field(default_factory=dict)
    score: float = Field(description="lower is better: weighted multi-objective cost")
    timeline: list[TimelinePoint] = Field(default_factory=list)
    duration_ms: float = 0.0
    diagnostics: dict[str, float] = Field(
        default_factory=dict, description="max_wait_ticks, min_battery, stockouts, zone_max_ratio…"
    )
    events_applied: int = 0


class RiskFinding(BaseModel):
    kind: Literal[
        "deadlock", "safety", "resource_exhaustion", "instability", "constraint", "regression", "capacity"
    ]
    severity: Severity
    message: str
    entity_ids: list[str] = Field(default_factory=list)


class RiskReport(BaseModel):
    level: RiskLevel
    score: float = Field(ge=0, le=1)
    findings: list[RiskFinding]
    stability: dict[str, float] = Field(default_factory=dict)
    checked_seeds: int = 1


class PlanModel(BaseModel):
    id: str
    name: str
    source: Literal["llm", "heuristic", "optimizer", "user"]
    description: str
    actions: list[ActionModel]
    optimized: bool = False
    feasible: bool = True
    validation_errors: list[str] = Field(default_factory=list)
    simulation: SimulationOutcome | None = None
    risk: RiskReport | None = None
    rank: int | None = None


class ApprovalModel(BaseModel):
    policy: Literal["auto", "human"]
    auto_approved: bool
    reason: str
    approved_by: str | None = None
    approved_tick: int | None = None


class DecisionModel(BaseModel):
    id: str
    created_tick: int
    sim_time: str
    trigger: str
    goal: str
    status: Literal["proposed", "approved", "rejected", "executed", "failed"]
    situation: dict[str, Any]
    baseline: SimulationOutcome | None
    candidates: list[PlanModel]
    recommended_plan_id: str | None
    approval: ApprovalModel
    explanation: str
    timings: dict[str, float]
    candidates_evaluated: int
    llm_used: bool
    llm_model: str | None = None


class DecisionRequest(BaseModel):
    goal: str = "Minimize SLA breaches and fulfillment delay"
    trigger: str = "manual"
    horizon_min: int = Field(default=90, ge=5, le=480)
    candidates: int | None = Field(default=None, ge=1, le=64)
    context: dict[str, Any] = Field(default_factory=dict)
    use_llm: bool | None = None


class DecisionActionRequest(BaseModel):
    action: Literal["approve", "reject", "execute"]
    plan_id: str | None = None
    note: str = ""
    actor: str = "operator"


# ------------------------------------------------------------------------------------------------
# what-if
# ------------------------------------------------------------------------------------------------

MutationType = Literal[
    "ROBOT_FAILURE",
    "REMOVE_ROBOTS",
    "ADD_ROBOTS",
    "DEMAND_MULTIPLIER",
    "DEMAND_BURST",
    "CLOSE_ZONE",
    "CLOSE_DOCK",
    "DISABLE_CHARGERS",
    "BLOCK_AISLE",
    "MOVE_INVENTORY",
    "WORKER_DELAY",
    "SET_SLA",
    "SET_BATCHING",
]


class MutationModel(BaseModel):
    type: MutationType
    params: dict[str, Any] = Field(default_factory=dict)
    at_min: float = Field(default=0.0, ge=0, description="minutes after scenario start")


class ScenarioModel(BaseModel):
    name: str
    description: str = ""
    mutations: list[MutationModel]


class WhatIfRequest(BaseModel):
    scenario: ScenarioModel
    strategies: list[str] = Field(default_factory=lambda: ["baseline", "optimized", "nexus_full"])
    horizon_min: int = Field(default=90, ge=5, le=480)
    seeds: int = Field(default=1, ge=1, le=5)
    include_current: bool = Field(default=True, description="also run the unmodified world as reference")


class WhatIfRun(BaseModel):
    strategy: str
    label: str
    seed: int
    kpis: KPIModel
    delta_vs_reference: dict[str, float] = Field(default_factory=dict)
    timeline: list[TimelinePoint] = Field(default_factory=list)
    duration_ms: float


class WhatIfResult(BaseModel):
    id: str
    status: Literal["queued", "running", "done", "failed"]
    scenario: ScenarioModel
    created_tick: int
    horizon_ticks: int
    reference: WhatIfRun | None = None
    runs: list[WhatIfRun] = Field(default_factory=list)
    best_strategy: str | None = None
    narrative: str = ""
    comparison: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


class WhatIfPreset(BaseModel):
    id: str
    name: str
    question: str
    description: str
    scenario: ScenarioModel


# ------------------------------------------------------------------------------------------------
# natural language
# ------------------------------------------------------------------------------------------------


class NLQRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    horizon_min: int = Field(default=60, ge=5, le=480)
    use_llm: bool | None = None


class NLQResponse(BaseModel):
    answer: str
    intent: Literal["explain", "whatif", "status", "forecast", "recommend", "entity", "unknown"]
    data: dict[str, Any] = Field(default_factory=dict)
    llm_used: bool
    model: str | None
    latency_ms: float
    suggestions: list[str] = Field(default_factory=list)


# ------------------------------------------------------------------------------------------------
# spatial
# ------------------------------------------------------------------------------------------------


class SpatialResponse(BaseModel):
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    zone_load: dict[str, dict[str, int]]
    zone_adjacency: dict[str, list[str]]


class EntityRelations(BaseModel):
    entity_id: str
    kind: str | None
    triples: list[list[str]]
    description: list[str]


# ------------------------------------------------------------------------------------------------
# snapshots / timeline
# ------------------------------------------------------------------------------------------------


class SnapshotInfo(BaseModel):
    tick: int
    sim_time: str
    digest: str
    kpis: dict[str, float]
    size_bytes: int


class TimelineResponse(BaseModel):
    points: list[TimelinePoint]
    snapshots: list[SnapshotInfo]
    notable_events: list[EventModel]


# ------------------------------------------------------------------------------------------------
# generic
# ------------------------------------------------------------------------------------------------


class OkResponse(BaseModel):
    ok: bool = True
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
