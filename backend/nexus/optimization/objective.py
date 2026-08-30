"""Multi-objective scoring.

NEXUS never optimises a single number in isolation. Every plan, what-if run and benchmark result is
scored with the same weighted cost so that "better" means the same thing everywhere::

    score(K) =  w_lateness      · SLA_breach_projected(K)          # dominant: 1 percentage point = 1.0
              + w_delivery_time · avg_fulfillment_min(K)
              + w_tail          · p95_fulfillment_min(K)
              + w_congestion    · congestion_index(K)
              + w_distance      · distance_total(K) / delivered(K)  # cells per delivered order
              + w_energy        · energy_total(K)   / delivered(K)  # battery % per delivered order
              + w_backlog       · orders_pending(K)

Lower is better. The default weights make SLA breaches the dominant term (10 % breach ≈ 10 points)
followed by fulfillment time (1 point per minute), so a plan only wins on distance/energy when it is
not paying for it in lateness. The same :class:`ObjectiveWeights` object also carries the per-pair
weights used inside the assignment cost matrix (``assign_*``), so the tactical solver and the
strategic scorer pull in the same direction.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from nexus.simulation.metrics import KPIs


@dataclass(slots=True)
class ObjectiveWeights:
    # ---- strategic (KPI-level) weights ---------------------------------------------------------
    lateness: float = 100.0  # × projected SLA breach rate (0..1)
    delivery_time: float = 1.0  # × average fulfillment minutes
    tail: float = 0.25  # × p95 fulfillment minutes
    congestion: float = 2.0  # × congestion index (excess robots per zone, averaged over ticks)
    distance: float = 0.005  # × cells travelled per delivered order
    energy: float = 0.1  # × battery percentage points consumed per delivered order
    backlog: float = 0.02  # × unassigned orders left at the end of the horizon
    priority: float = 1.0  # global multiplier on priority weights (assignment + sequencing)
    # ---- tactical (assignment-pair) weights -----------------------------------------------------
    assign_delivery_per_min: float = 1.0  # × predicted trip minutes
    assign_lateness_per_min: float = 4.0  # × predicted minutes late × priority weight
    assign_battery: float = 0.2  # × battery percentage points the trip needs
    assign_congestion: float = 3.0  # × Σ over touched zones of max(0, occupancy − capacity) / capacity
    assign_routing: float = 1.0  # × routing-policy penalty sampled at the pick cells

    def to_dict(self) -> dict[str, float]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, float]) -> ObjectiveWeights:
        return ObjectiveWeights(
            **{k: float(v) for k, v in d.items() if k in ObjectiveWeights.__dataclass_fields__}
        )


DEFAULT_WEIGHTS = ObjectiveWeights()


def score_breakdown(kpis: KPIs, weights: ObjectiveWeights | None = None) -> dict[str, float]:
    """Each term of the objective, so explanations can say *why* a plan scored what it scored."""
    w = weights or DEFAULT_WEIGHTS
    delivered = max(1, kpis.orders_delivered)
    return {
        "lateness": round(w.lateness * kpis.sla_breach_rate_projected, 4),
        "delivery_time": round(w.delivery_time * kpis.avg_fulfillment_min, 4),
        "tail": round(w.tail * kpis.p95_fulfillment_min, 4),
        "congestion": round(w.congestion * kpis.congestion_index, 4),
        "distance": round(w.distance * kpis.distance_total / delivered, 4),
        "energy": round(w.energy * kpis.energy_total / delivered, 4),
        "backlog": round(w.backlog * kpis.orders_pending, 4),
    }


def score_kpis(kpis: KPIs, weights: ObjectiveWeights | None = None) -> float:
    """Weighted multi-objective cost of a KPI set. Lower is better."""
    return round(sum(score_breakdown(kpis, weights).values()), 4)


def compare_scores(
    reference: KPIs, candidate: KPIs, weights: ObjectiveWeights | None = None
) -> dict[str, float]:
    """Score delta (candidate − reference) overall and per term; negative means the candidate is better."""
    ref = score_breakdown(reference, weights)
    cand = score_breakdown(candidate, weights)
    out = {k: round(cand[k] - ref[k], 4) for k in ref}
    out["total"] = round(sum(out.values()), 4)
    return out
