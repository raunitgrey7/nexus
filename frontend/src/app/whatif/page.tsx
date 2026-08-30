"use client";

import { useEffect, useState } from "react";
import { FlaskConical, Play, RefreshCw } from "lucide-react";
import { useShallow } from "zustand/react/shallow";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Panel } from "@/components/ui/Panel";
import { Empty, ErrorBanner, Skeleton } from "@/components/ui/State";
import { Tabs } from "@/components/ui/Table";
import { ScenarioBuilder } from "@/components/whatif/ScenarioBuilder";
import { WhatIfResults } from "@/components/whatif/WhatIfResults";
import { describeMutation } from "@/components/whatif/mutations";
import { C, WHATIF_STATUS_COLORS, strategyColor } from "@/lib/colors";
import { pct } from "@/lib/format";
import type { WhatIfPreset, WhatIfRequest } from "@/lib/types";
import { useTwinStore } from "@/store/twinStore";
import { currentWhatIf, useWhatIfStore } from "@/store/whatifStore";

const DEFAULT_STRATEGIES = ["baseline", "optimized", "ai_planner", "nexus_full"];

const EMPTY_REQUEST: WhatIfRequest = {
  scenario: { name: "Custom scenario", description: "", mutations: [] },
  strategies: ["baseline", "optimized", "nexus_full"],
  horizon_min: 90,
  seeds: 1,
  include_current: true,
};

export default function WhatIfPage() {
  const { presets, history, load, loaded, loading, error, run, running, select, current } = useWhatIfStore(
    useShallow((s) => ({
      presets: s.presets,
      history: s.history,
      load: s.load,
      loaded: s.loaded,
      loading: s.loading,
      error: s.error,
      run: s.run,
      running: s.running,
      select: s.select,
      current: currentWhatIf(s),
    })),
  );
  const strategies = useTwinStore((s) => s.strategies);
  const [request, setRequest] = useState<WhatIfRequest>(EMPTY_REQUEST);
  const [tab, setTab] = useState<"presets" | "history">("presets");

  useEffect(() => {
    if (!loaded) void load();
  }, [loaded, load]);

  const strategyNames = strategies.length ? strategies.map((s) => s.name) : DEFAULT_STRATEGIES;

  const applyPreset = (p: WhatIfPreset, andRun: boolean) => {
    const req: WhatIfRequest = { ...request, scenario: structuredClone(p.scenario) };
    setRequest(req);
    if (andRun) void run(req);
  };

  return (
    <div className="flex h-full min-h-0">
      <aside className="flex w-[340px] shrink-0 flex-col border-r border-border bg-panel">
        <Tabs
          tabs={[
            { id: "presets", label: "Presets", count: presets.length },
            { id: "history", label: "History", count: history.length },
          ]}
          value={tab}
          onChange={setTab}
        />
        {error && <ErrorBanner message={error} onRetry={() => void load()} className="m-2" />}
        <div className="min-h-0 flex-1 overflow-y-auto">
          {tab === "presets" &&
            (loading && presets.length === 0 ? (
              <Skeleton lines={6} />
            ) : presets.length === 0 ? (
              <Empty title="No presets" hint="GET /api/whatif/presets returned nothing." />
            ) : (
              <ul className="flex flex-col gap-2 p-2">
                {presets.map((p) => (
                  <li key={p.id} className="panel p-2.5 transition-colors hover:border-accent/50">
                    <div className="flex items-start gap-2">
                      <FlaskConical size={14} className="mt-0.5 shrink-0 text-violet" />
                      <div className="min-w-0 flex-1">
                        <div className="text-xs font-semibold text-text">{p.question}</div>
                        <div className="mt-0.5 text-[11px] text-muted">{p.description}</div>
                        <div className="mt-1.5 flex flex-wrap gap-1">
                          {p.scenario.mutations.map((m, i) => (
                            <Badge key={i} color={C.warn} mono>
                              {describeMutation(m)}
                            </Badge>
                          ))}
                        </div>
                        <div className="mt-2 flex gap-1">
                          <Button size="xs" variant="primary" icon={<Play size={11} />} loading={running} onClick={() => applyPreset(p, true)}>
                            Run
                          </Button>
                          <Button size="xs" variant="ghost" onClick={() => applyPreset(p, false)}>
                            Load into builder
                          </Button>
                        </div>
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            ))}
          {tab === "history" &&
            (history.length === 0 ? (
              <Empty title="No what-if runs yet" hint="Results appear here as evaluations complete." />
            ) : (
              <ul>
                {history.map((r) => {
                  const active = r.id === current?.id;
                  const best = r.runs.find((x) => x.strategy === r.best_strategy);
                  return (
                    <li key={r.id}>
                      <button
                        type="button"
                        onClick={() => select(r.id)}
                        className={`w-full border-b border-border/70 px-3 py-2 text-left hover:bg-panel-2 ${active ? "bg-accent/10" : ""}`}
                        style={{ boxShadow: active ? `inset 3px 0 0 ${C.accent}` : undefined }}
                      >
                        <div className="flex items-center gap-2">
                          <span className="num text-xs font-semibold text-text">{r.id}</span>
                          <Badge color={WHATIF_STATUS_COLORS[r.status]}>{r.status}</Badge>
                          <span className="num ml-auto text-[10px] text-dim">t={r.created_tick}</span>
                        </div>
                        <div className="truncate text-[11px] text-muted">{r.scenario.name}</div>
                        {r.best_strategy && (
                          <div className="mt-0.5 text-[10px]">
                            <span style={{ color: strategyColor(r.best_strategy) }}>{r.best_strategy}</span>
                            <span className="num text-dim"> · {pct(best?.kpis.sla_breach_rate_projected)} breach</span>
                          </div>
                        )}
                      </button>
                    </li>
                  );
                })}
              </ul>
            ))}
        </div>
      </aside>

      <section className="flex min-w-0 flex-1 flex-col gap-3 overflow-y-auto p-4">
        <Panel
          label="Scenario builder"
          actions={
            <>
              <Button size="xs" variant="ghost" icon={<RefreshCw size={11} />} onClick={() => setRequest(EMPTY_REQUEST)}>
                Clear
              </Button>
              <Button size="xs" variant="primary" icon={<Play size={11} />} loading={running} onClick={() => void run(request)} disabled={request.strategies.length === 0}>
                Run scenario
              </Button>
            </>
          }
        >
          <ScenarioBuilder value={request} onChange={setRequest} strategies={strategyNames} />
        </Panel>
        <Panel label="Results" className="min-h-[300px]">
          <WhatIfResults result={current} />
        </Panel>
      </section>
    </div>
  );
}
