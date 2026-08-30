"""KPI computation. The single definition used by the engine, agents, what-if engine, API, UI,
benchmarks and the pitch deck (see ROADMAP.md → KPI definitions)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from nexus.twin.entities import OrderStatus
from nexus.twin.world import WorldState


@dataclass(slots=True)
class KPIs:
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
    sla_breach_rate: float  # among delivered orders
    sla_breach_rate_projected: float  # delivered-late + open-overdue over delivered + open
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def headline(self) -> dict[str, float]:
        return {
            "sla_breach_rate_projected": self.sla_breach_rate_projected,
            "avg_fulfillment_min": self.avg_fulfillment_min,
            "throughput_per_hour": self.throughput_per_hour,
            "robot_utilization": self.robot_utilization,
            "congestion_index": self.congestion_index,
        }


def _percentile(values: list[int], pct: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    k = (len(values) - 1) * pct
    lo = int(k)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def compute_kpis(world: WorldState, since_tick: int = 0) -> KPIs:
    tick = world.clock.tick
    ts = world.clock.tick_seconds
    horizon_ticks = max(1, tick - since_tick)
    sim_hours = horizon_ticks * ts / 3600.0

    delivered_ft: list[int] = []
    late = 0
    lateness = 0
    created = 0
    delivered = 0
    cancelled = 0
    open_orders = 0
    pending = 0
    overdue_open = 0
    for order in world.orders.values():
        if order.created_tick >= since_tick:
            created += 1
        if (
            order.status == OrderStatus.DELIVERED
            and order.delivered_tick is not None
            and order.delivered_tick >= since_tick
        ):
            delivered += 1
            delivered_ft.append(order.delivered_tick - order.created_tick)
            if order.delivered_tick > order.deadline_tick:
                late += 1
                lateness += order.delivered_tick - order.deadline_tick
        elif order.status == OrderStatus.CANCELLED and (order.cancelled_tick or 0) >= since_tick:
            cancelled += 1
        elif order.status.open:
            open_orders += 1
            if order.status == OrderStatus.PENDING:
                pending += 1
            if tick > order.deadline_tick:
                overdue_open += 1

    robots = list(world.robots.values())
    operational = sum(1 for r in robots if r.status.operational)
    st = world.stats
    util_den = max(1, st.operational_robot_ticks)
    minutes = ts / 60.0
    avg_ft = (sum(delivered_ft) / len(delivered_ft) * minutes) if delivered_ft else 0.0
    completed_or_overdue = delivered + open_orders
    return KPIs(
        tick=tick,
        sim_hours=round(sim_hours, 4),
        orders_created=created,
        orders_delivered=delivered,
        orders_open=open_orders,
        orders_pending=pending,
        orders_late=late,
        orders_overdue_open=overdue_open,
        orders_cancelled=cancelled,
        avg_fulfillment_min=round(avg_ft, 3),
        p50_fulfillment_min=round(_percentile(delivered_ft, 0.50) * minutes, 3),
        p95_fulfillment_min=round(_percentile(delivered_ft, 0.95) * minutes, 3),
        sla_breach_rate=round(late / delivered, 5) if delivered else 0.0,
        sla_breach_rate_projected=round((late + overdue_open) / completed_or_overdue, 5)
        if completed_or_overdue
        else 0.0,
        throughput_per_hour=round(delivered / sim_hours, 3) if sim_hours > 0 else 0.0,
        robot_utilization=round(st.productive_robot_ticks / util_den, 5),
        robot_availability=round(operational / len(robots), 5) if robots else 0.0,
        robots_total=len(robots),
        robots_operational=operational,
        distance_total=st.distance_total,
        energy_total=round(st.energy_total, 3),
        congestion_index=round(st.congestion_ticks_total / max(1, st.ticks), 5),
        wait_ticks_per_robot_hour=round(
            st.wait_ticks_total / max(1e-9, (st.operational_robot_ticks * ts / 3600.0)), 3
        ),
        replans=st.replans_total,
        failures=st.failures_total,
        charging_sessions=st.charging_sessions,
        inventory_units=world.inventory_units(),
        avg_lateness_min=round(lateness / late * minutes, 3) if late else 0.0,
    )


def kpi_delta(before: KPIs, after: KPIs) -> dict[str, float]:
    """Signed change of the headline KPIs (after − before)."""
    b, a = before.headline(), after.headline()
    return {k: round(a[k] - b[k], 5) for k in b}
