"""Prometheus metrics for the live twin and the agent runtime."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

REGISTRY = CollectorRegistry(auto_describe=True)

TICK = Gauge("nexus_sim_tick", "Current simulated tick", registry=REGISTRY)
TICK_RATE = Gauge("nexus_sim_ticks_per_second", "Configured ticks per wall second", registry=REGISTRY)
ORDERS_OPEN = Gauge("nexus_orders_open", "Open orders", registry=REGISTRY)
ORDERS_PENDING = Gauge("nexus_orders_pending", "Pending (unassigned) orders", registry=REGISTRY)
SLA_BREACH = Gauge("nexus_sla_breach_projected", "Projected SLA breach ratio", registry=REGISTRY)
AVG_FULFILLMENT = Gauge("nexus_avg_fulfillment_minutes", "Average fulfillment time (min)", registry=REGISTRY)
THROUGHPUT = Gauge("nexus_throughput_per_hour", "Delivered orders per simulated hour", registry=REGISTRY)
UTILIZATION = Gauge("nexus_robot_utilization", "Share of productive robot-ticks", registry=REGISTRY)
CONGESTION = Gauge("nexus_congestion_index", "Mean zone over-capacity", registry=REGISTRY)
ROBOTS_OPERATIONAL = Gauge("nexus_robots_operational", "Operational robots", registry=REGISTRY)
ROBOTS_TOTAL = Gauge("nexus_robots_total", "Total robots", registry=REGISTRY)
ZONE_OCCUPANCY = Gauge("nexus_zone_occupancy", "Robots per zone", ["zone"], registry=REGISTRY)
EVENTS = Counter("nexus_events_total", "Events emitted", ["type"], registry=REGISTRY)
DECISIONS = Counter("nexus_decisions_total", "Decisions produced", ["status"], registry=REGISTRY)
SIMULATIONS = Counter("nexus_simulations_total", "Forked-world simulations run", ["kind"], registry=REGISTRY)
PLANNING_LATENCY = Histogram(
    "nexus_planning_latency_seconds",
    "Decision pipeline latency",
    buckets=(0.5, 1, 2, 5, 10, 20, 40, 80),
    registry=REGISTRY,
)
WHATIF_LATENCY = Histogram(
    "nexus_whatif_latency_seconds",
    "What-if evaluation latency",
    buckets=(0.5, 1, 2, 5, 10, 20, 40, 80),
    registry=REGISTRY,
)
STEP_DURATION = Histogram(
    "nexus_step_duration_seconds",
    "Engine step wall time",
    buckets=(0.0005, 0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1),
    registry=REGISTRY,
)
LLM_CALLS = Counter("nexus_llm_calls_total", "LLM calls", ["outcome"], registry=REGISTRY)


def update_kpis(kpis: dict[str, float], tick: int, zone_occupancy: dict[str, int] | None = None) -> None:
    TICK.set(tick)
    ORDERS_OPEN.set(kpis.get("orders_open", 0))
    ORDERS_PENDING.set(kpis.get("orders_pending", 0))
    SLA_BREACH.set(kpis.get("sla_breach_rate_projected", 0.0))
    AVG_FULFILLMENT.set(kpis.get("avg_fulfillment_min", 0.0))
    THROUGHPUT.set(kpis.get("throughput_per_hour", 0.0))
    UTILIZATION.set(kpis.get("robot_utilization", 0.0))
    CONGESTION.set(kpis.get("congestion_index", 0.0))
    ROBOTS_OPERATIONAL.set(kpis.get("robots_operational", 0))
    ROBOTS_TOTAL.set(kpis.get("robots_total", 0))
    if zone_occupancy:
        for zone, n in zone_occupancy.items():
            ZONE_OCCUPANCY.labels(zone=zone).set(n)


def render() -> bytes:
    return generate_latest(REGISTRY)
