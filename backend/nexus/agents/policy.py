"""Approval policy: who may release a plan to the live operation."""

from __future__ import annotations

from nexus.api.schemas import ApprovalModel, PlanModel, SimulationOutcome

RISK_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


class ApprovalPolicy:
    def __init__(self, auto_max_risk: str = "LOW", min_gain: float = 0.02) -> None:
        self.auto_max_risk = auto_max_risk.upper()
        self.min_gain = min_gain

    def decide(self, plan: PlanModel, baseline: SimulationOutcome | None, tick: int) -> ApprovalModel:
        if all(a.type == "NOOP" for a in plan.actions):
            return ApprovalModel(
                policy="auto",
                auto_approved=True,
                reason="No action needed: doing nothing is the best evaluated option.",
                approved_by="policy",
                approved_tick=tick,
            )
        if plan.simulation is None:
            return ApprovalModel(policy="human", auto_approved=False, reason="Plan was not simulated.")
        if plan.risk is None:
            return ApprovalModel(policy="human", auto_approved=False, reason="Plan has no risk assessment.")
        gain = 0.0
        if baseline is not None:
            gain = baseline.kpis.sla_breach_rate_projected - plan.simulation.kpis.sla_breach_rate_projected
        risk_ok = RISK_RANK.get(plan.risk.level, 3) <= RISK_RANK.get(self.auto_max_risk, 0)
        if risk_ok and gain >= self.min_gain:
            return ApprovalModel(
                policy="auto",
                auto_approved=True,
                reason=f"Risk {plan.risk.level} and projected SLA breach improves by {gain:.1%} (≥ {self.min_gain:.0%}).",
                approved_by="policy",
                approved_tick=tick,
            )
        if not risk_ok:
            return ApprovalModel(
                policy="human",
                auto_approved=False,
                reason=f"Risk {plan.risk.level} exceeds the auto-approval limit ({self.auto_max_risk}); operator approval required.",
            )
        return ApprovalModel(
            policy="human",
            auto_approved=False,
            reason=f"Projected gain {gain:.1%} is below the auto-approval threshold ({self.min_gain:.0%}); operator approval required.",
        )
