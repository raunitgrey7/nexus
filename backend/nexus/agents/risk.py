"""Risk agent: judges a simulated plan before it can be approved.

Findings are produced from the simulation diagnostics (deadlock / stuck robots, zone over-capacity,
battery exhaustion, charger starvation, stockouts), from a comparison with the do-nothing baseline
(regressions), from validation errors (constraints), from capacity headroom, and from **stability**:
the recommended plan is re-simulated under perturbed random streams and the spread of the SLA
outcome is measured. A plan whose benefit disappears under a different order sequence is not a plan.
"""

from __future__ import annotations

import statistics
from typing import Any

from nexus.api.schemas import PlanModel, RiskFinding, RiskLevel, RiskReport, SimulationOutcome

SEVERITY_WEIGHT = {"info": 0.0, "low": 0.12, "medium": 0.3, "high": 0.55, "critical": 1.0}


class RiskAgent:
    def __init__(self, stable_std: float = 0.025, unstable_std: float = 0.05) -> None:
        self.stable_std = stable_std
        self.unstable_std = unstable_std

    def assess(
        self,
        plan: PlanModel,
        outcome: SimulationOutcome,
        baseline: SimulationOutcome | None,
        stability_outcomes: list[SimulationOutcome] | None = None,
        world_summary: dict[str, Any] | None = None,
    ) -> RiskReport:
        findings: list[RiskFinding] = []
        d = outcome.diagnostics
        k = outcome.kpis

        # ---- constraints -----------------------------------------------------------------------
        if not plan.feasible:
            findings.append(
                RiskFinding(
                    kind="constraint",
                    severity="critical",
                    message="Plan is infeasible after validation.",
                    entity_ids=[],
                )
            )
        elif plan.validation_errors:
            findings.append(
                RiskFinding(
                    kind="constraint",
                    severity="info",
                    message="Some actions were dropped or clamped: " + "; ".join(plan.validation_errors[:3]),
                )
            )

        # ---- deadlock / stuck robots -----------------------------------------------------------
        max_wait = d.get("max_wait_ticks", 0.0)
        stuck = d.get("stuck_robot_ticks", 0.0)
        if max_wait >= 60 or stuck > 600:
            findings.append(
                RiskFinding(
                    kind="deadlock",
                    severity="high",
                    message=f"Robots blocked for up to {max_wait:.0f} s; {stuck:.0f} stuck robot-seconds — deadlock pattern.",
                )
            )
        elif max_wait >= 25 or stuck > 150:
            findings.append(
                RiskFinding(
                    kind="deadlock",
                    severity="medium",
                    message=f"Robots blocked for up to {max_wait:.0f} s ({stuck:.0f} stuck robot-seconds).",
                )
            )

        # ---- safety ----------------------------------------------------------------------------
        ratio = d.get("zone_max_ratio", 0.0)
        overcap_share = d.get("overcap_share", 0.0)
        if overcap_share >= 0.05:
            findings.append(
                RiskFinding(
                    kind="safety",
                    severity="high",
                    message=f"Zones exceed 2× capacity for {overcap_share:.0%} of the horizon (peak {ratio:.1f}×) — safety policy violated.",
                )
            )
        elif overcap_share >= 0.01:
            findings.append(
                RiskFinding(
                    kind="safety",
                    severity="medium",
                    message=f"Zones exceed 2× capacity for {overcap_share:.1%} of the horizon (peak {ratio:.1f}×).",
                )
            )
        elif ratio >= 1.5:
            findings.append(
                RiskFinding(
                    kind="safety", severity="low", message=f"Transient peak zone load {ratio:.1f}× capacity."
                )
            )

        # ---- resources -------------------------------------------------------------------------
        min_batt = d.get("min_battery", 100.0)
        if min_batt < 5:
            findings.append(
                RiskFinding(
                    kind="resource_exhaustion",
                    severity="high",
                    message=f"A robot's battery drops to {min_batt:.0f}% — depletion risk.",
                )
            )
        elif min_batt < 12:
            findings.append(
                RiskFinding(
                    kind="resource_exhaustion",
                    severity="medium",
                    message=f"Minimum battery {min_batt:.0f}% during the horizon.",
                )
            )
        if d.get("charger_starved_ticks", 0) > 120:
            findings.append(
                RiskFinding(
                    kind="resource_exhaustion",
                    severity="medium",
                    message="Robots wait for chargers — charging capacity is saturated.",
                )
            )
        if d.get("stockouts", 0) > 0:
            findings.append(
                RiskFinding(
                    kind="resource_exhaustion",
                    severity="low",
                    message=f"{d['stockouts']:.0f} pick attempts hit stock-outs.",
                )
            )
        if d.get("failures", 0) > 0 and (world_summary or {}).get("robots_failed", 0) == 0:
            findings.append(
                RiskFinding(
                    kind="resource_exhaustion",
                    severity="medium",
                    message=f"{d['failures']:.0f} robot failures occurred in simulation (battery depletion?).",
                )
            )

        # ---- regression vs baseline ------------------------------------------------------------
        if baseline is not None:
            bk = baseline.kpis
            if k.sla_breach_rate_projected > bk.sla_breach_rate_projected + 0.005:
                findings.append(
                    RiskFinding(
                        kind="regression",
                        severity="high",
                        message=f"SLA breach worsens: {bk.sla_breach_rate_projected:.1%} → {k.sla_breach_rate_projected:.1%}.",
                    )
                )
            if bk.throughput_per_hour > 0 and k.throughput_per_hour < 0.95 * bk.throughput_per_hour:
                findings.append(
                    RiskFinding(
                        kind="regression",
                        severity="medium",
                        message=f"Throughput drops {1 - k.throughput_per_hour / bk.throughput_per_hour:.0%} vs doing nothing.",
                    )
                )
            if k.congestion_index > 1.5 * max(0.05, bk.congestion_index) and k.congestion_index > 0.2:
                findings.append(
                    RiskFinding(
                        kind="regression",
                        severity="low",
                        message=f"Congestion index rises {bk.congestion_index:.2f} → {k.congestion_index:.2f}.",
                    )
                )

        # ---- capacity --------------------------------------------------------------------------
        if k.robot_utilization > 0.97:
            findings.append(
                RiskFinding(
                    kind="capacity",
                    severity="low",
                    message="Fleet runs at >97% utilization — no slack for further disruptions.",
                )
            )

        # ---- stability -------------------------------------------------------------------------
        stability: dict[str, float] = {}
        checked = 1
        if stability_outcomes:
            breaches = [outcome.kpis.sla_breach_rate_projected] + [
                o.kpis.sla_breach_rate_projected for o in stability_outcomes
            ]
            fts = [outcome.kpis.avg_fulfillment_min] + [
                o.kpis.avg_fulfillment_min for o in stability_outcomes
            ]
            std = statistics.pstdev(breaches)
            stability = {
                "sla_breach_mean": round(statistics.fmean(breaches), 5),
                "sla_breach_std": round(std, 5),
                "sla_breach_min": round(min(breaches), 5),
                "sla_breach_max": round(max(breaches), 5),
                "avg_fulfillment_std": round(statistics.pstdev(fts), 4),
            }
            checked = len(breaches)
            if std > self.unstable_std:
                findings.append(
                    RiskFinding(
                        kind="instability",
                        severity="high",
                        message=f"Outcome varies widely across seeds (σ = {std:.1%} SLA breach).",
                    )
                )
            elif std > self.stable_std:
                findings.append(
                    RiskFinding(
                        kind="instability",
                        severity="medium",
                        message=f"Moderate outcome variance across seeds (σ = {std:.1%}).",
                    )
                )
            else:
                findings.append(
                    RiskFinding(
                        kind="instability",
                        severity="info",
                        message=f"Stable across {checked} seeds (σ = {std:.1%}).",
                    )
                )

        score = min(1.0, sum(SEVERITY_WEIGHT[f.severity] for f in findings))
        level: RiskLevel = "LOW"
        if any(f.severity == "critical" for f in findings) or score >= 0.75:
            level = "CRITICAL"
        elif any(f.severity == "high" for f in findings) or score >= 0.45:
            level = "HIGH"
        elif score >= 0.2:
            level = "MEDIUM"
        return RiskReport(
            level=level, score=round(score, 3), findings=findings, stability=stability, checked_seeds=checked
        )
