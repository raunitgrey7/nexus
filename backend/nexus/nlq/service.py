"""Natural-language console service: question → intent → engine query → grounded answer."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any, cast

from nexus.api.schemas import NLQResponse, WhatIfRequest
from nexus.core.logging import get_logger
from nexus.llm.prompts import NLQ_ANSWER_SYSTEM, NLQ_INTENT_SYSTEM
from nexus.nlq.explain import attribute_delay, explain_text
from nexus.nlq.router import SUGGESTIONS, build_scenario, classify
from nexus.twin.spatial import SpatialGraph

if TYPE_CHECKING:
    from nexus.runtime.live import LiveRuntime

log = get_logger("nexus.nlq")


class NLQService:
    def __init__(self, runtime: LiveRuntime) -> None:
        self.runtime = runtime

    def ask(self, question: str, horizon_min: int = 60, use_llm: bool | None = None) -> NLQResponse:
        t0 = time.perf_counter()
        rt = self.runtime
        llm = rt.llm if (use_llm is None or use_llm) else None
        intent, params = classify(question)
        if intent == "unknown" and llm is not None and llm.available():
            refined = llm.chat(
                [{"role": "system", "content": NLQ_INTENT_SYSTEM}, {"role": "user", "content": question}],
                json_schema={
                    "type": "object",
                    "properties": {"intent": {"type": "string"}, "params": {"type": "object"}},
                    "required": ["intent"],
                },
                max_tokens=200,
                timeout_s=20.0,
            )
            if refined:
                try:
                    parsed = json.loads(refined)
                    if parsed.get("intent") in (
                        "explain",
                        "whatif",
                        "status",
                        "forecast",
                        "recommend",
                        "entity",
                    ):
                        intent = parsed["intent"]
                        params.update(parsed.get("params") or {})
                except json.JSONDecodeError:
                    pass
        data: dict[str, Any] = {"intent": intent, "params": params}
        answer = ""
        try:
            if intent == "whatif":
                answer, data = self._whatif(question, params, horizon_min, data)
            elif intent == "explain":
                with rt.lock:
                    attribution = attribute_delay(rt.engine.world)
                answer = explain_text(attribution)
                data["attribution"] = attribution
            elif intent == "forecast":
                forecast = rt.forecast(horizon_min)
                answer = forecast.summary
                data["forecast"] = forecast.model_dump()
            elif intent == "recommend":
                decision = rt.decide(
                    goal=question,
                    trigger="nlq",
                    horizon_min=min(horizon_min, 45),
                    candidates=5,
                    use_llm=use_llm,
                )
                answer = decision.explanation
                data["decision_id"] = decision.id
                data["recommended_plan"] = next(
                    (p.model_dump() for p in decision.candidates if p.id == decision.recommended_plan_id),
                    None,
                )
            elif intent == "entity":
                answer, data = self._entity(params, data)
            elif intent == "status":
                answer, data = self._status(data)
            else:
                answer = "I can explain delays, run what-if scenarios, forecast the next hour, recommend a plan, or describe any robot, order or zone. Try one of the suggestions."
        except Exception as exc:
            log.exception("nlq.failed", intent=intent)
            answer = f"I could not complete that ({type(exc).__name__}: {str(exc)[:120]})."
        llm_used = False
        model = None
        if (
            llm is not None
            and llm.available()
            and intent in ("explain", "forecast", "status", "entity", "whatif")
        ):
            polished = llm.chat(
                [
                    {"role": "system", "content": NLQ_ANSWER_SYSTEM},
                    {
                        "role": "user",
                        "content": f"Question: {question}\n\nGrounded answer (authoritative): {answer}\n\nSupporting data (JSON): {json.dumps(_compact(data))[:3500]}\n\nRewrite the grounded answer as a helpful reply. Keep every number.",
                    },
                ],
                max_tokens=350,
                timeout_s=25.0,
            )
            if polished and len(polished) < 4 * max(80, len(answer)):
                answer = polished.strip()
                llm_used = True
                model = llm.model
        return NLQResponse(
            answer=answer,
            intent=cast(Any, intent),
            data=data,
            llm_used=llm_used,
            model=model,
            latency_ms=round((time.perf_counter() - t0) * 1000, 1),
            suggestions=SUGGESTIONS[:4],
        )  # type: ignore[arg-type]

    # ---- intents -------------------------------------------------------------------------------
    def _whatif(
        self, question: str, params: dict[str, Any], horizon_min: int, data: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        rt = self.runtime
        with rt.lock:
            scenario = build_scenario(params, rt.engine.world)
            current = getattr(rt.engine.strategy, "name", "baseline")
        strategies = [current] + [s for s in ("optimized", "nexus_full") if s != current]
        result = rt.whatif.run(
            WhatIfRequest(
                scenario=scenario,
                strategies=strategies,
                horizon_min=horizon_min,
                seeds=1,
                include_current=True,
            )
        )
        ref = result.reference
        answer = result.narrative
        if ref is not None and result.comparison:
            cur = next((r for r in result.comparison if r["strategy"] == current), result.comparison[0])
            best = result.comparison[0]
            extra = f" Under this scenario the current strategy ({current}) projects an SLA breach of {cur['sla_breach_rate_projected']:.1%} (vs {ref.kpis.sla_breach_rate_projected:.1%} today)."
            if best["strategy"] != current:
                extra += f" Switching to {best['strategy']} would bring it to {best['sla_breach_rate_projected']:.1%}."
            answer = answer + extra
        data["whatif"] = result.model_dump()
        return answer, data

    def _entity(self, params: dict[str, Any], data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        rt = self.runtime
        with rt.lock:
            world = rt.engine.world
            entity_id = (
                (params.get("robot_ids") or [None])[0]
                or params.get("order_id")
                or params.get("task_id")
                or params.get("charger")
                or params.get("worker")
                or params.get("dock")
                or params.get("zone")
            )
            info = rt.entity(entity_id) if entity_id else None
            if info is None or entity_id is None:
                return (
                    "I could not find that entity. Use ids like R07, ORD-000123, zone C, D2, CH01 or W03.",
                    data,
                )
            sg = SpatialGraph(world)
            relations = sg.describe(str(entity_id), limit=8)
        data["entity"] = info
        data["relations"] = relations
        kind = info.get("kind")
        e = info.get("entity", {})
        if kind == "robot":
            answer = f"{entity_id} is {e['status']} in zone {e['zone_id']} at cell {tuple(e['cell'])}, battery {e['battery']:.0f}%, task {e.get('task_id') or 'none'}, {e['orders_completed']} orders completed, {e['distance']} cells traveled."
            if e.get("failure_cause"):
                answer += f" It failed ({e['failure_cause']}) at tick {e['failed_tick']}."
        elif kind == "order":
            answer = f"{entity_id} is {e['status']} (priority {e['priority_name']}, {len(e['lines'])} lines / {e['items']} items), created at tick {e['created_tick']}, deadline tick {e['deadline_tick']}, robot {e.get('robot_id') or 'unassigned'}, dock {e.get('dock_id') or '-'}."
        elif kind == "zone":
            answer = f"{e['name']} ({e['kind']}): {info.get('robots', 0)} robots inside (capacity {e['capacity']}), {info.get('open_orders', 0)} open orders need it, closed={e['closed']}."
        else:
            answer = f"{entity_id}: " + ", ".join(f"{k}={v}" for k, v in list(e.items())[:8])
        if relations:
            answer += " Relations: " + "; ".join(relations[:5]) + "."
        return answer, data

    def _status(self, data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        rt = self.runtime
        with rt.lock:
            world = rt.engine.world
            k = rt.engine.kpis()
            s = world.summary()
        data["kpis"] = k.to_dict()
        data["summary"] = s
        answer = (
            f"At {s['sim_time'][11:16]}: {s['orders_open']} open orders ({s['orders_pending']} pending), {s['robots_operational']}/{s['robots_total']} robots operational "
            f"({s['robots_charging']} charging), projected SLA breach {k.sla_breach_rate_projected:.1%}, average fulfillment {k.avg_fulfillment_min:.1f} min (p95 {k.p95_fulfillment_min:.1f}), "
            f"throughput {k.throughput_per_hour:.0f} orders/h, utilization {k.robot_utilization:.0%}, congestion index {k.congestion_index:.2f}."
        )
        if s["robots_failed"]:
            answer += f" {s['robots_failed']} robot(s) are down."
        if s["closed_zones"]:
            answer += f" Closed zones: {', '.join(s['closed_zones'])}."
        return answer, data


def _compact(data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in data.items():
        if k == "whatif" and isinstance(v, dict):
            out[k] = {
                "narrative": v.get("narrative"),
                "comparison": v.get("comparison"),
                "best_strategy": v.get("best_strategy"),
            }
        elif k == "forecast" and isinstance(v, dict):
            out[k] = {
                "summary": v.get("summary"),
                "demand": v.get("demand"),
                "bottlenecks": v.get("bottlenecks", [])[:3],
            }
        elif k in ("attribution", "kpis", "summary", "entity", "relations", "params", "recommended_plan"):
            out[k] = v
    return out
