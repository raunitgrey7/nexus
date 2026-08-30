"use client";

import { useEffect } from "react";
import { BrainCircuit, RefreshCw } from "lucide-react";
import { useShallow } from "zustand/react/shallow";
import { DecisionDetail } from "@/components/decisions/DecisionDetail";
import { Badge, RiskBadge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Empty, ErrorBanner, Skeleton } from "@/components/ui/State";
import { C, DECISION_STATUS_COLORS } from "@/lib/colors";
import { pct, simTimeShort } from "@/lib/format";
import { recommendedPlan, selectedDecision, useDecisionStore } from "@/store/decisionStore";

export default function DecisionsPage() {
  const { decisions, load, loading, loaded, error, select, selectedId, create, creating, decision } = useDecisionStore(
    useShallow((s) => ({
      decisions: s.decisions,
      load: s.load,
      loading: s.loading,
      loaded: s.loaded,
      error: s.error,
      select: s.select,
      selectedId: s.selectedId,
      create: s.create,
      creating: s.creating,
      decision: selectedDecision(s),
    })),
  );
  useEffect(() => {
    if (!loaded) void load();
  }, [loaded, load]);

  return (
    <div className="flex h-full min-h-0">
      <aside className="flex w-[320px] shrink-0 flex-col border-r border-border bg-panel">
        <header className="flex items-center gap-2 border-b border-border px-3 py-2">
          <span className="label">Decisions</span>
          <span className="num text-[10px] text-dim">{decisions.length}</span>
          <div className="ml-auto flex items-center gap-1">
            <Button size="xs" variant="ghost" icon={<RefreshCw size={11} />} onClick={() => void load()} loading={loading} aria-label="Reload" />
            <Button size="xs" variant="violet" icon={<BrainCircuit size={12} />} loading={creating} onClick={() => void create({ trigger: "manual" })}>
              Run pipeline
            </Button>
          </div>
        </header>
        {error && <ErrorBanner message={error} onRetry={() => void load()} className="m-2" />}
        {loading && decisions.length === 0 ? (
          <Skeleton lines={6} />
        ) : decisions.length === 0 ? (
          <Empty title="No decisions yet" hint="Run the pipeline here, or press “Decide now” on the Live Twin after injecting a fault." />
        ) : (
          <ul className="min-h-0 flex-1 overflow-y-auto">
            {decisions.map((d) => {
              const rec = recommendedPlan(d);
              const active = d.id === selectedId;
              return (
                <li key={d.id}>
                  <button
                    type="button"
                    onClick={() => select(d.id)}
                    className={`w-full border-b border-border/70 px-3 py-2 text-left transition-colors hover:bg-panel-2 ${active ? "bg-accent/10" : ""}`}
                    style={{ boxShadow: active ? `inset 3px 0 0 ${C.accent}` : undefined }}
                  >
                    <div className="flex items-center gap-2">
                      <span className="num text-xs font-semibold text-text">{d.id}</span>
                      <Badge color={DECISION_STATUS_COLORS[d.status]}>{d.status}</Badge>
                      <span className="num ml-auto text-[10px] text-dim">{simTimeShort(d.sim_time)}</span>
                    </div>
                    <div className="mt-0.5 truncate text-[11px] text-muted">{d.trigger}</div>
                    <div className="mt-1 flex items-center gap-2 text-[10px]">
                      <span className="num">
                        <span className="text-bad">{pct(d.baseline?.kpis.sla_breach_rate_projected)}</span>
                        <span className="text-dim"> → </span>
                        <span className="text-good">{pct(rec?.simulation?.kpis.sla_breach_rate_projected)}</span>
                      </span>
                      <RiskBadge level={rec?.risk?.level} />
                      <span className="num ml-auto text-dim">{d.candidates_evaluated} cand.</span>
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </aside>
      <section className="min-w-0 flex-1 overflow-y-auto p-4">
        {decision ? (
          <div className="mx-auto max-w-5xl">
            <DecisionDetail decision={decision} />
          </div>
        ) : loading ? (
          <Skeleton lines={10} />
        ) : (
          <Empty title="Select a decision" hint="Details, candidate comparison, risk findings and the simulated horizon appear here." />
        )}
      </section>
    </div>
  );
}
