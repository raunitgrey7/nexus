"use client";

import { Star } from "lucide-react";
import { Badge, RiskBadge } from "@/components/ui/Badge";
import { Bar } from "@/components/ui/Bar";
import { Table, Td, Th } from "@/components/ui/Table";
import { C } from "@/lib/colors";
import { fixed, num, pct } from "@/lib/format";
import type { DecisionModel, PlanModel } from "@/lib/types";

const SOURCE_COLORS: Record<PlanModel["source"], string> = { llm: C.violet, heuristic: C.muted, optimizer: C.blue, user: C.warn };

interface Props {
  decision: DecisionModel;
  selectedPlanId: string | null;
  onSelect: (id: string | null) => void;
  compact?: boolean;
}

export function CandidatesTable({ decision, selectedPlanId, onSelect, compact }: Props) {
  const plans = decision.candidates.slice().sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99));
  const maxBreach = Math.max(
    0.01,
    decision.baseline?.kpis.sla_breach_rate_projected ?? 0,
    ...plans.map((p) => p.simulation?.kpis.sla_breach_rate_projected ?? 0),
  );
  const breachColor = (v: number) => (v >= 0.12 ? C.bad : v >= 0.06 ? C.warn : C.good);

  const row = (p: PlanModel | null, isBaseline: boolean) => {
    const k = isBaseline ? decision.baseline?.kpis : p?.simulation?.kpis;
    const id = isBaseline ? "__baseline" : p!.id;
    const recommended = !isBaseline && p!.id === decision.recommended_plan_id;
    const selected = selectedPlanId === id || (selectedPlanId === null && recommended);
    const breach = k?.sla_breach_rate_projected;
    return (
      <tr
        key={id}
        onClick={() => onSelect(isBaseline ? "__baseline" : p!.id)}
        className={`cursor-pointer transition-colors hover:bg-panel-2 ${selected ? "bg-accent/5" : ""}`}
        style={{ boxShadow: recommended ? `inset 3px 0 0 ${C.violet}` : selected ? `inset 3px 0 0 ${C.accent}` : undefined }}
      >
        <Td className="num text-dim">{isBaseline ? "—" : (p!.rank ?? "—")}</Td>
        <Td className="max-w-[260px]">
          <div className="flex items-center gap-1.5">
            {recommended && <Star size={11} className="shrink-0 text-violet" fill={C.violet} />}
            <span className={`truncate ${recommended ? "font-semibold text-text" : "text-text"}`} title={isBaseline ? "Do nothing" : p!.name}>
              {isBaseline ? "Baseline (no intervention)" : p!.name}
            </span>
          </div>
          {!compact && !isBaseline && p!.validation_errors.length > 0 && (
            <div className="truncate text-[10px] text-bad">{p!.validation_errors.join("; ")}</div>
          )}
        </Td>
        <Td>
          {isBaseline ? (
            <Badge color={C.dim}>simulation</Badge>
          ) : (
            <span className="flex items-center gap-1">
              <Badge color={SOURCE_COLORS[p!.source]}>{p!.source}</Badge>
              {p!.optimized && <Badge color={C.blue}>opt</Badge>}
              {!p!.feasible && <Badge color={C.bad}>infeasible</Badge>}
            </span>
          )}
        </Td>
        <Td>
          {breach === undefined ? (
            <span className="text-dim">—</span>
          ) : (
            <div className="flex items-center gap-2">
              <Bar value={breach / maxBreach} color={breachColor(breach)} className="w-20" />
              <span className="num text-[11px]" style={{ color: breachColor(breach) }}>
                {pct(breach)}
              </span>
            </div>
          )}
        </Td>
        <Td align="right" className="text-muted">
          {k ? `${fixed(k.avg_fulfillment_min)}m` : "—"}
        </Td>
        <Td align="right" className="text-muted">
          {k ? num(k.throughput_per_hour) : "—"}
        </Td>
        <Td align="right" className="text-muted">
          {k ? fixed(k.congestion_index, 2) : "—"}
        </Td>
        <Td>{isBaseline ? <span className="text-dim">—</span> : <RiskBadge level={p!.risk?.level} />}</Td>
      </tr>
    );
  };

  return (
    <Table>
      <thead>
        <tr>
          <Th>#</Th>
          <Th>Plan</Th>
          <Th>Source</Th>
          <Th>SLA breach (proj.)</Th>
          <Th align="right">Fulfil.</Th>
          <Th align="right">Thr./h</Th>
          <Th align="right">Cong.</Th>
          <Th>Risk</Th>
        </tr>
      </thead>
      <tbody>
        {decision.baseline && row(null, true)}
        {plans.map((p) => row(p, false))}
      </tbody>
    </Table>
  );
}
