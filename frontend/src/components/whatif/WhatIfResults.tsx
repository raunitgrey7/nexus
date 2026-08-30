"use client";

import { useMemo } from "react";
import { Trophy } from "lucide-react";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { TOOLTIP_STYLE, TimelineCompareChart, type Series } from "@/components/decisions/TimelineCompareChart";
import { Badge } from "@/components/ui/Badge";
import { Panel } from "@/components/ui/Panel";
import { Empty, Loading } from "@/components/ui/State";
import { Table, Td, Th } from "@/components/ui/Table";
import { C, WHATIF_STATUS_COLORS, strategyColor } from "@/lib/colors";
import { delta, fixed, num, pct } from "@/lib/format";
import type { KPIModel, WhatIfResult, WhatIfRun } from "@/lib/types";
import { describeMutation } from "./mutations";

interface StrategyRow {
  strategy: string;
  runs: number;
  breach: number;
  fulfil: number;
  throughput: number;
  congestion: number;
  utilization: number;
  dBreach?: number;
  dThroughput?: number;
}

function aggregate(result: WhatIfResult): StrategyRow[] {
  const by = new Map<string, WhatIfRun[]>();
  for (const r of result.runs) by.set(r.strategy, [...(by.get(r.strategy) ?? []), r]);
  const rows: StrategyRow[] = [];
  const avg = (rs: WhatIfRun[], f: (k: KPIModel) => number) => rs.reduce((a, r) => a + f(r.kpis), 0) / rs.length;
  for (const [strategy, rs] of by) {
    const ref = result.reference?.kpis;
    const breach = avg(rs, (k) => k.sla_breach_rate_projected);
    const throughput = avg(rs, (k) => k.throughput_per_hour);
    rows.push({
      strategy,
      runs: rs.length,
      breach,
      fulfil: avg(rs, (k) => k.avg_fulfillment_min),
      throughput,
      congestion: avg(rs, (k) => k.congestion_index),
      utilization: avg(rs, (k) => k.robot_utilization),
      dBreach: ref ? breach - ref.sla_breach_rate_projected : undefined,
      dThroughput: ref ? throughput - ref.throughput_per_hour : undefined,
    });
  }
  return rows.sort((a, b) => a.breach - b.breach);
}

function MetricBars({ rows, dataKey, title, fmt }: { rows: StrategyRow[]; dataKey: keyof StrategyRow; title: string; fmt: (v: unknown) => string }) {
  return (
    <div className="min-w-0">
      <div className="label mb-1">{title}</div>
      <ResponsiveContainer width="100%" height={150}>
        <BarChart data={rows} margin={{ top: 4, right: 4, left: -18, bottom: 0 }}>
          <CartesianGrid stroke={C.border} strokeDasharray="2 4" vertical={false} />
          <XAxis dataKey="strategy" stroke={C.dim} tickLine={false} interval={0} tick={{ fontSize: 9 }} />
          <YAxis stroke={C.dim} tickLine={false} tickFormatter={(v: number) => (dataKey === "breach" ? `${Math.round(v * 100)}%` : String(v))} />
          <Tooltip {...TOOLTIP_STYLE} formatter={(v: unknown) => [fmt(v), title]} cursor={{ fill: "rgba(255,255,255,0.03)" }} />
          <Bar dataKey={dataKey} isAnimationActive={false} radius={[2, 2, 0, 0]}>
            {rows.map((r) => (
              <Cell key={r.strategy} fill={strategyColor(r.strategy)} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function WhatIfResults({ result }: { result: WhatIfResult | null }) {
  const rows = useMemo(() => (result ? aggregate(result) : []), [result]);
  const series = useMemo<Series[]>(() => {
    if (!result) return [];
    const out: Series[] = [];
    if (result.reference) out.push({ id: "reference", name: "Reference", color: C.dim, points: result.reference.timeline, dashed: true });
    const seen = new Set<string>();
    for (const r of result.runs) {
      if (seen.has(r.strategy)) continue;
      seen.add(r.strategy);
      out.push({ id: r.strategy, name: r.strategy, color: strategyColor(r.strategy), points: r.timeline });
    }
    return out;
  }, [result]);

  if (!result) return <Empty title="No scenario selected" hint="Pick a preset or build a custom scenario and press Run." />;

  const pending = result.status === "queued" || result.status === "running";
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="num text-sm font-semibold text-text">{result.id}</span>
        <Badge color={WHATIF_STATUS_COLORS[result.status]} pulse={pending}>
          {result.status}
        </Badge>
        <span className="text-xs text-text">{result.scenario.name}</span>
        <span className="num text-[10px] text-dim">
          t={result.created_tick} · horizon {Math.round(result.horizon_ticks / 60)} min
        </span>
        {result.best_strategy && (
          <Badge color={strategyColor(result.best_strategy)} variant="solid" className="ml-auto">
            <Trophy size={10} /> best: {result.best_strategy}
          </Badge>
        )}
      </div>
      <div className="flex flex-wrap gap-1">
        {result.scenario.mutations.map((m, i) => (
          <Badge key={i} color={C.warn} mono>
            {describeMutation(m)}
          </Badge>
        ))}
      </div>
      {pending && <Loading label={result.status === "queued" ? "Queued — forking worlds" : "Simulating strategies in forked worlds"} />}
      {result.status === "failed" && <div className="rounded border border-bad/40 bg-bad/10 p-2 text-xs text-bad">{result.error ?? "Evaluation failed"}</div>}
      {result.status === "done" && (
        <>
          {result.narrative && (
            <Panel label="Narrative">
              <p className="text-xs leading-relaxed text-text/90">{result.narrative}</p>
            </Panel>
          )}
          <Panel label="Comparison" padded={false}>
            <Table>
              <thead>
                <tr>
                  <Th>Strategy</Th>
                  <Th align="right">Runs</Th>
                  <Th align="right">SLA breach</Th>
                  <Th align="right">Δ ref</Th>
                  <Th align="right">Fulfil.</Th>
                  <Th align="right">Thr./h</Th>
                  <Th align="right">Δ ref</Th>
                  <Th align="right">Cong.</Th>
                  <Th align="right">Util.</Th>
                </tr>
              </thead>
              <tbody>
                {result.reference && (
                  <tr className="text-dim">
                    <Td>
                      <Badge color={C.dim}>reference</Badge> <span className="text-[10px]">{result.reference.label}</span>
                    </Td>
                    <Td align="right">1</Td>
                    <Td align="right">{pct(result.reference.kpis.sla_breach_rate_projected)}</Td>
                    <Td align="right">—</Td>
                    <Td align="right">{fixed(result.reference.kpis.avg_fulfillment_min)}m</Td>
                    <Td align="right">{num(result.reference.kpis.throughput_per_hour)}</Td>
                    <Td align="right">—</Td>
                    <Td align="right">{fixed(result.reference.kpis.congestion_index, 2)}</Td>
                    <Td align="right">{pct(result.reference.kpis.robot_utilization, 0)}</Td>
                  </tr>
                )}
                {rows.map((r) => (
                  <tr key={r.strategy} className={r.strategy === result.best_strategy ? "bg-accent/5" : ""} style={{ boxShadow: r.strategy === result.best_strategy ? `inset 3px 0 0 ${strategyColor(r.strategy)}` : undefined }}>
                    <Td>
                      <span className="font-semibold" style={{ color: strategyColor(r.strategy) }}>
                        {r.strategy}
                      </span>
                    </Td>
                    <Td align="right">{r.runs}</Td>
                    <Td align="right" style={{ color: r.breach >= 0.12 ? C.bad : r.breach >= 0.06 ? C.warn : C.good }}>
                      {pct(r.breach)}
                    </Td>
                    <Td align="right" className={r.dBreach !== undefined && r.dBreach > 0 ? "text-bad" : "text-good"}>
                      {delta(r.dBreach, { pct: true })}
                    </Td>
                    <Td align="right">{fixed(r.fulfil)}m</Td>
                    <Td align="right">{num(r.throughput)}</Td>
                    <Td align="right" className={r.dThroughput !== undefined && r.dThroughput < 0 ? "text-bad" : "text-good"}>
                      {delta(r.dThroughput, { digits: 0 })}
                    </Td>
                    <Td align="right">{fixed(r.congestion, 2)}</Td>
                    <Td align="right">{pct(r.utilization, 0)}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </Panel>
          <Panel label="KPIs per strategy">
            <div className="grid grid-cols-3 gap-3">
              <MetricBars rows={rows} dataKey="breach" title="SLA breach (projected)" fmt={(v) => (typeof v === "number" ? pct(v) : String(v))} />
              <MetricBars rows={rows} dataKey="fulfil" title="Avg fulfilment (min)" fmt={(v) => (typeof v === "number" ? `${fixed(v)} min` : String(v))} />
              <MetricBars rows={rows} dataKey="throughput" title="Throughput / h" fmt={(v) => (typeof v === "number" ? num(v) : String(v))} />
            </div>
          </Panel>
          <Panel label="Timelines" title="projected SLA breach · open orders (dashed)">
            <TimelineCompareChart series={series} height={230} />
          </Panel>
        </>
      )}
    </div>
  );
}
