"""What-If engine: evaluates a scenario under several strategies on forked worlds.

    CURRENT STATE ──fork──► + scenario mutations ──► strategy A ──► KPIs
                    ├──fork──► + scenario mutations ──► strategy B ──► KPIs
                    └──fork──► (no mutation, current strategy) ──► reference KPIs

Jobs are independent and run through the same job runner as plan simulation (process pool when
available). Results are compared on the shared KPI definitions and scored with the optimization
objective; a deterministic narrative summarises the comparison.
"""

from __future__ import annotations

import pickle
import threading
import time
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from nexus.agents.simulator import SimJob, run_jobs, to_outcome
from nexus.api.schemas import ActionModel, PlanModel, WhatIfRequest, WhatIfResult, WhatIfRun
from nexus.core.logging import get_logger
from nexus.simulation.strategies import make_strategy
from nexus.whatif.presets import PRESETS
from nexus.whatif.scenarios import describe_scenario, scenario_faults

if TYPE_CHECKING:
    from nexus.simulation.engine import SimulationEngine

log = get_logger("nexus.whatif")
NOOP = PlanModel(
    id="whatif-noop", name="scenario", source="user", description="", actions=[ActionModel(type="NOOP")]
)


class WhatIfEngine:
    def __init__(
        self,
        engine_provider: Callable[[], SimulationEngine],
        workers: int | None = None,
        on_done: Callable[[WhatIfResult], None] | None = None,
    ) -> None:
        self.engine_provider = engine_provider
        self.workers = workers
        self.on_done = on_done
        self.results: dict[str, WhatIfResult] = {}
        self.order: list[str] = []
        self._lock = threading.Lock()

    # ---- synchronous evaluation ----------------------------------------------------------------
    def run(self, request: WhatIfRequest, result_id: str | None = None) -> WhatIfResult:
        engine = self.engine_provider()
        world = engine.world.fork("whatif")
        start = world.clock.tick
        horizon = int(request.horizon_min * 60 / world.clock.tick_seconds)
        live_faults = [f.to_dict() for f in engine.faults.remaining()]
        scenario_dicts = [f.to_dict() for f in scenario_faults(world, request.scenario, start)]
        world_bytes = world.snapshot_bytes()
        current_strategy_bytes = pickle.dumps(engine.strategy)
        current_name = getattr(engine.strategy, "name", "baseline")

        jobs: list[SimJob] = []
        labels: list[tuple[str, str, int]] = []  # (strategy, label, seed)
        if request.include_current:
            jobs.append(
                SimJob(
                    world_bytes,
                    current_strategy_bytes,
                    NOOP.model_dump(),
                    horizon,
                    0,
                    live_faults,
                    60,
                    "reference",
                )
            )
            labels.append((current_name, f"reference ({current_name}, no scenario)", 0))
        for name in request.strategies:
            strategy = engine.strategy if name == current_name else make_strategy(name)
            strategy_bytes = pickle.dumps(strategy)
            for s in range(request.seeds):
                salt = 0 if s == 0 else 101 * s
                jobs.append(
                    SimJob(
                        world_bytes,
                        strategy_bytes,
                        NOOP.model_dump(),
                        horizon,
                        salt,
                        live_faults + scenario_dicts,
                        60,
                        f"{name}#{s}",
                    )
                )
                labels.append(
                    (name, f"{name} under scenario" + (f" (seed {s})" if request.seeds > 1 else ""), s)
                )

        t0 = time.perf_counter()
        results = run_jobs(jobs, self.workers)
        elapsed = (time.perf_counter() - t0) * 1000
        reference_res = results[0] if request.include_current else None
        runs: list[WhatIfRun] = []
        for (name, label, seed), res in zip(
            labels[1 if request.include_current else 0 :],
            results[1 if request.include_current else 0 :],
            strict=True,
        ):
            outcome = to_outcome(res, reference_res)
            runs.append(
                WhatIfRun(
                    strategy=name,
                    label=label,
                    seed=seed,
                    kpis=outcome.kpis,
                    delta_vs_reference=outcome.delta_vs_baseline,
                    timeline=outcome.timeline,
                    duration_ms=outcome.duration_ms,
                )
            )
        reference = None
        if reference_res is not None:
            ro = to_outcome(reference_res, reference_res)
            reference = WhatIfRun(
                strategy=current_name,
                label=labels[0][1],
                seed=0,
                kpis=ro.kpis,
                delta_vs_reference={},
                timeline=ro.timeline,
                duration_ms=ro.duration_ms,
            )
        # aggregate per strategy over seeds
        by_strategy: dict[str, list[WhatIfRun]] = {}
        for r in runs:
            by_strategy.setdefault(r.strategy, []).append(r)
        comparison: list[dict[str, Any]] = []
        for name, rs in by_strategy.items():
            n = len(rs)
            row: dict[str, Any] = {
                "strategy": name,
                "runs": n,
                "sla_breach_rate_projected": sum(r.kpis.sla_breach_rate_projected for r in rs) / n,
                "avg_fulfillment_min": sum(r.kpis.avg_fulfillment_min for r in rs) / n,
                "p95_fulfillment_min": sum(r.kpis.p95_fulfillment_min for r in rs) / n,
                "throughput_per_hour": sum(r.kpis.throughput_per_hour for r in rs) / n,
                "robot_utilization": sum(r.kpis.robot_utilization for r in rs) / n,
                "congestion_index": sum(r.kpis.congestion_index for r in rs) / n,
                "orders_open_end": sum(r.kpis.orders_open for r in rs) / n,
            }
            scores = (
                [
                    res["score"]
                    for (nm, _, _), res in zip(labels, results, strict=True)
                    if nm == name and _ != labels[0][1]
                ]
                if request.include_current
                else [res["score"] for (nm, _, _), res in zip(labels, results, strict=True) if nm == name]
            )
            row["score"] = sum(scores) / len(scores) if scores else 0.0
            comparison.append(row)
        comparison.sort(key=lambda r: (r["score"], r["strategy"]))
        best: str | None = str(comparison[0]["strategy"]) if comparison else None
        result = WhatIfResult(
            id=result_id or f"WI-{uuid.uuid4().hex[:8]}",
            status="done",
            scenario=request.scenario,
            created_tick=start,
            horizon_ticks=horizon,
            reference=reference,
            runs=runs,
            best_strategy=best,
            comparison=[
                {k: (round(v, 5) if isinstance(v, float) else v) for k, v in row.items()}
                for row in comparison
            ],
            narrative=self._narrative(request, reference, comparison, best, elapsed),
        )
        return result

    @staticmethod
    def _narrative(
        request: WhatIfRequest,
        reference: WhatIfRun | None,
        comparison: list[dict[str, Any]],
        best: str | None,
        elapsed_ms: float,
    ) -> str:
        parts = [
            f"Scenario “{request.scenario.name}”: {describe_scenario(request.scenario)}. Horizon {request.horizon_min} min, {len(comparison)} strategies × {request.seeds} seed(s) simulated in {elapsed_ms / 1000:.1f}s."
        ]
        if reference is not None:
            parts.append(
                f"Reference (no scenario, {reference.strategy}): SLA breach {reference.kpis.sla_breach_rate_projected:.1%}, avg fulfillment {reference.kpis.avg_fulfillment_min:.1f} min, throughput {reference.kpis.throughput_per_hour:.0f}/h."
            )
        for row in comparison:
            parts.append(
                f"{row['strategy']}: SLA breach {row['sla_breach_rate_projected']:.1%}, avg fulfillment {row['avg_fulfillment_min']:.1f} min, throughput {row['throughput_per_hour']:.0f}/h, congestion {row['congestion_index']:.2f}."
            )
        if best and comparison:
            worst = comparison[-1]
            first = comparison[0]
            if len(comparison) > 1 and worst["sla_breach_rate_projected"] > 0:
                parts.append(
                    f"Best strategy: {best} — SLA breach {first['sla_breach_rate_projected']:.1%} vs {worst['sla_breach_rate_projected']:.1%} for {worst['strategy']}."
                )
            else:
                parts.append(f"Best strategy: {best}.")
        return " ".join(parts)

    # ---- async jobs ----------------------------------------------------------------------------
    def submit(self, request: WhatIfRequest) -> WhatIfResult:
        rid = f"WI-{uuid.uuid4().hex[:8]}"
        placeholder = WhatIfResult(
            id=rid,
            status="queued",
            scenario=request.scenario,
            created_tick=self.engine_provider().world.clock.tick,
            horizon_ticks=int(request.horizon_min * 60),
        )
        with self._lock:
            self.results[rid] = placeholder
            self.order.append(rid)
        thread = threading.Thread(
            target=self._run_async, args=(request, rid), name=f"whatif-{rid}", daemon=True
        )
        thread.start()
        return placeholder

    def _run_async(self, request: WhatIfRequest, rid: str) -> None:
        with self._lock:
            self.results[rid].status = "running"
        try:
            result = self.run(request, rid)
        except Exception as exc:
            log.exception("whatif.failed", id=rid)
            with self._lock:
                self.results[rid].status = "failed"
                self.results[rid].error = str(exc)[:300]
            return
        with self._lock:
            self.results[rid] = result
        if self.on_done is not None:
            try:
                self.on_done(result)
            except Exception as exc:
                log.warning("whatif.on_done_failed", error=str(exc)[:120])

    def get(self, rid: str) -> WhatIfResult | None:
        return self.results.get(rid)

    def history(self, limit: int = 20) -> list[WhatIfResult]:
        return [self.results[i] for i in reversed(self.order[-limit:])]

    @staticmethod
    def presets() -> list[Any]:
        return PRESETS
