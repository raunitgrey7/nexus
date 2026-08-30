"""Explanation agent: numbers-first operator briefings for decisions.

The template is the source of truth (every number comes from the decision record); the LLM, when
available, only rewrites it for fluency and is discarded if it drops or changes a number.
"""

from __future__ import annotations

import re
from typing import Any

from nexus.agents.situation import Situation
from nexus.api.schemas import DecisionModel, PlanModel
from nexus.llm.client import LLMClient
from nexus.llm.prompts import EXPLAIN_SYSTEM

ACTION_PHRASES = {
    "REASSIGN_TASKS": lambda p: (
        f"reassign work from {', '.join(p.get('from_robots') or ['idle robots'])} to {', '.join(p.get('to_robots') or ['nearest robots'])}"
        + (f" in zones {'/'.join(p['zones'])}" if p.get("zones") else "")
    ),
    "REPRIORITIZE_ORDERS": lambda p: (
        f"prioritise {p.get('priority_at_least', 'zone').lower()}-priority orders"
        if "priority_at_least" in p
        else f"prioritise orders for zones {'/'.join(p.get('zones', []))}"
    ),
    "SEND_TO_CHARGE": lambda p: (
        f"send {', '.join(p.get('robot_ids', []))} to charge"
        + (" after their current task" if p.get("after_current_task") else "")
    ),
    "REROUTE_AVOID_ZONE": lambda p: (
        f"reroute traffic away from zone {'/'.join(p.get('zones', []))} for {p.get('duration_min', 30)} min"
    ),
    "PREFER_CORRIDOR": lambda p: f"route through corridor {'/'.join(p.get('corridors', []))}",
    "REPOSITION_INVENTORY": lambda p: (
        f"pre-position {p.get('skus', 6)} hot SKUs from zone {p.get('from_zone')} to zone {p.get('to_zone')}"
    ),
    "SET_BATCHING": lambda p: f"batch {p.get('orders_per_trip')} orders per trip",
    "SET_ZONE_CAPACITY": lambda p: (
        "tighten soft capacity of " + ", ".join(f"zone {z} to {c}" for z, c in (p.get("zones") or {}).items())
    ),
    "CLOSE_ZONE": lambda p: f"close zone {p.get('zone_id')}",
    "OPEN_ZONE": lambda p: f"open zone {p.get('zone_id')}",
    "ADD_ROBOTS": lambda p: f"add {p.get('count')} robots",
    "REMOVE_ROBOTS": lambda p: f"remove {', '.join(p.get('robot_ids', []))}",
    "DISPATCH_WORKER": lambda p: f"dispatch {p.get('worker_id')} to dock {p.get('dock_id')}",
    "CANCEL_TASKS": lambda p: f"cancel {len(p.get('task_ids', []))} tasks",
    "SET_STRATEGY": lambda p: f"switch to the {p.get('name')} scheduler",
    "NOOP": lambda p: "take no action",
}


def _phrase(a: Any) -> str:
    fn = ACTION_PHRASES.get(a.type)
    return fn(a.params) if fn else str(a.type).lower().replace("_", " ")


def describe_actions(plan: PlanModel) -> str:
    parts = [_phrase(a) for a in plan.actions]
    if not parts:
        return "take no action"
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def template_explanation(decision: DecisionModel, situation: Situation | None, horizon_min: int) -> str:
    plan = next((p for p in decision.candidates if p.id == decision.recommended_plan_id), None)
    base = decision.baseline
    sentences: list[str] = []
    if situation is not None and base is not None:
        now_ft = situation.kpis.avg_fulfillment_min
        if situation.failed_robots:
            r = situation.failed_robots[0]
            pct = (base.kpis.avg_fulfillment_min / now_ft - 1.0) if now_ft > 0 else 0.0
            sentences.append(
                f"{r['id']} failure ({str(r['cause']).replace('_', ' ')}) will increase average order fulfillment time from {now_ft:.1f} to {base.kpis.avg_fulfillment_min:.1f} min ({max(0.0, pct):+.0%}) over the next {horizon_min} minutes "
                f"(projected SLA breach {base.kpis.sla_breach_rate_projected:.1%} without intervention)."
            )
        elif situation.congested_zones:
            z = situation.congested_zones[0]
            sentences.append(
                f"Zone {z['zone']} congestion ({z['robots']}/{z['capacity']} robots, {z['open_orders']} open orders need it) projects an SLA breach of {base.kpis.sla_breach_rate_projected:.1%} over the next {horizon_min} minutes."
            )
        else:
            sentences.append(
                f"Without intervention the next {horizon_min} minutes project an SLA breach of {base.kpis.sla_breach_rate_projected:.1%} and {base.kpis.avg_fulfillment_min:.1f} min average fulfillment."
            )
    sentences.append(
        f"I evaluated {decision.candidates_evaluated} candidate plans and {decision.situation.get('allocations_considered', 0)} task allocations in {decision.timings.get('total_ms', 0) / 1000:.1f}s (planning {decision.timings.get('planning_ms', 0):.0f} ms, simulation {decision.timings.get('simulation_ms', 0):.0f} ms)."
    )
    if plan is not None and plan.simulation is not None:
        sim = plan.simulation
        if base is not None:
            sentences.append(
                f"Recommended plan #{plan.rank or 1} — {plan.name}: {describe_actions(plan)}. Estimated impact: SLA breach {base.kpis.sla_breach_rate_projected:.1%} → {sim.kpis.sla_breach_rate_projected:.1%}, "
                f"average fulfillment {base.kpis.avg_fulfillment_min:.1f} → {sim.kpis.avg_fulfillment_min:.1f} min, throughput {base.kpis.throughput_per_hour:.0f} → {sim.kpis.throughput_per_hour:.0f} orders/h."
            )
        else:
            sentences.append(f"Recommended plan — {plan.name}: {describe_actions(plan)}.")
        if plan.risk is not None:
            notable = [f.message for f in plan.risk.findings if f.severity not in ("info",)]
            risk_txt = f"Risk {plan.risk.level}" + (f" ({notable[0]})" if notable else "")
            sentences.append(
                f"{risk_txt}; {'auto-approved' if decision.approval.auto_approved else 'awaiting operator approval'} — {decision.approval.reason}"
            )
    else:
        sentences.append("No plan improved on doing nothing; no action is recommended.")
    return " ".join(sentences)


_NUM = re.compile(r"\d+(?:\.\d+)?")


def explain_decision(
    decision: DecisionModel, situation: Situation | None, horizon_min: int, llm: LLMClient | None = None
) -> str:
    text = template_explanation(decision, situation, horizon_min)
    if llm is None or not llm.available():
        return text
    polished = llm.complete(
        f"Rewrite this briefing for an operations manager, keeping every number exactly:\n\n{text}",
        system=EXPLAIN_SYSTEM,
        max_tokens=400,
        timeout_s=25.0,
    )
    if not polished:
        return text
    original_numbers = set(_NUM.findall(text))
    polished_numbers = set(_NUM.findall(polished))
    if not original_numbers.issubset(polished_numbers) or len(polished) > 2.5 * len(text):
        return text
    return polished.strip()
