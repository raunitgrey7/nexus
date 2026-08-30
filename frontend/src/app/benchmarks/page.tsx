"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";
import { Bar, BarChart, CartesianGrid, Legend, PolarAngleAxis, PolarGrid, PolarRadiusAxis, Radar, RadarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { TOOLTIP_STYLE } from "@/components/decisions/TimelineCompareChart";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Panel } from "@/components/ui/Panel";
import { Select } from "@/components/ui/Select";
import { Empty, ErrorBanner, Skeleton } from "@/components/ui/State";
import { Table, Td, Th } from "@/components/ui/Table";
import { ApiError, errorMessage } from "@/lib/api";
import { api } from "@/lib/client";
import { C, strategyColor } from "@/lib/colors";
import { fixed, num, pct, titleCase } from "@/lib/format";
import type { BenchmarkResults } from "@/lib/types";

interface KpiDef {
  key: string;
  label: string;
  lowerBetter: boolean;
  fmt: (v: number) => string;
}

const KPI_DEFS: KpiDef[] = [
  { key: "sla_breach_rate", label: "SLA breach rate", lowerBetter: true, fmt: (v) => pct(v) },
  { key: "avg_fulfillment_min", label: "Avg fulfilment (min)", lowerBetter: true, fmt: (v) => fixed(v, 2) },
  { key: "throughput_per_hour", label: "Throughput / h", lowerBetter: false, fmt: (v) => num(v) },
  { key: "robot_utilization", label: "Robot utilization", lowerBetter: false, fmt: (v) => pct(v, 0) },
  { key: "congestion_index", label: "Congestion index", lowerBetter: true, fmt: (v) => fixed(v, 2) },
  { key: "distance_total", label: "Distance (cells)", lowerBetter: true, fmt: (v) => num(v) },
  { key: "energy_total", label: "Energy (% pts)", lowerBetter: true, fmt: (v) => num(v) },
  { key: "planning_latency_s", label: "Planning latency (s)", lowerBetter: true, fmt: (v) => fixed(v, 2) },
];

const SCALE_ORDER = ["tiny", "small", "medium", "large"];
const STRATEGY_ORDER = ["baseline", "optimized", "ai_planner", "nexus_full"];

interface Flat {
  scale: string;
  strategy: string;
  runs: number | null;
  mean: Record<string, number>;
  std: Record<string, number>;
}

function isNum(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

/** Compact axis labels: 61k, 1.2k, 0.42, 7 */
function axisLabel(v: number, ratio: boolean): string {
  if (ratio) return `${Math.round(v * 100)}%`;
  if (Math.abs(v) >= 1000) return `${(v / 1000).toFixed(Math.abs(v) >= 10_000 ? 0 : 1)}k`;
  if (Number.isInteger(v)) return String(v);
  return v.toFixed(Math.abs(v) < 1 ? 2 : 1);
}

function flatten(b: BenchmarkResults): Flat[] {
  const out: Flat[] = [];
  const scales = b.scales && typeof b.scales === "object" ? b.scales : {};
  for (const [scale, sc] of Object.entries(scales)) {
    const strategies = sc && typeof sc === "object" && sc.strategies && typeof sc.strategies === "object" ? sc.strategies : {};
    for (const [strategy, res] of Object.entries(strategies)) {
      if (!res || typeof res !== "object") continue;
      const mean: Record<string, number> = {};
      const std: Record<string, number> = {};
      for (const [k, v] of Object.entries(res.kpis_mean ?? {})) if (isNum(v)) mean[k] = v;
      for (const [k, v] of Object.entries(res.kpis_std ?? {})) if (isNum(v)) std[k] = v;
      const runs = isNum(res.runs) ? res.runs : Array.isArray(res.runs) ? res.runs.length : null;
      out.push({ scale, strategy, runs, mean, std });
    }
  }
  const order = (arr: string[], v: string) => (arr.indexOf(v) === -1 ? 99 : arr.indexOf(v));
  return out.sort((a, b) => order(SCALE_ORDER, a.scale) - order(SCALE_ORDER, b.scale) || order(STRATEGY_ORDER, a.strategy) - order(STRATEGY_ORDER, b.strategy));
}

export default function BenchmarksPage() {
  const [data, setData] = useState<BenchmarkResults | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [radarScale, setRadarScale] = useState<string>("");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await api.benchmarks());
    } catch (e) {
      // the backend answers 404 until a benchmark run has produced results/latest.json
      if (e instanceof ApiError && e.status === 404) setData({});
      else setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  const flat = useMemo(() => (data ? flatten(data) : []), [data]);
  const scales = useMemo(() => [...new Set(flat.map((f) => f.scale))], [flat]);
  const strategies = useMemo(() => [...new Set(flat.map((f) => f.strategy))], [flat]);
  const kpis = useMemo(() => KPI_DEFS.filter((k) => flat.some((f) => k.key in f.mean)), [flat]);
  const effectiveRadarScale = radarScale && scales.includes(radarScale) ? radarScale : (scales[scales.length - 1] ?? "");

  const radarRows = useMemo(() => {
    const rows = flat.filter((f) => f.scale === effectiveRadarScale);
    return kpis.map((k) => {
      const vals = rows.map((r) => r.mean[k.key]).filter(isNum);
      const min = Math.min(...vals);
      const max = Math.max(...vals);
      const row: Record<string, number | string> = { kpi: k.label };
      for (const r of rows) {
        const v = r.mean[k.key];
        if (!isNum(v)) continue;
        // 1 = best strategy for this KPI, min-max normalised
        row[r.strategy] = max === min ? 1 : k.lowerBetter ? (max - v) / (max - min) : (v - min) / (max - min);
      }
      return row;
    });
  }, [flat, kpis, effectiveRadarScale]);

  const summaryRows = useMemo(() => (Array.isArray(data?.summary_table) ? data!.summary_table : []), [data]);
  const summaryCols = useMemo(() => {
    const cols: string[] = [];
    for (const r of summaryRows) for (const k of Object.keys(r)) if (!cols.includes(k)) cols.push(k);
    return cols;
  }, [summaryRows]);

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 overflow-y-auto p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="label">Benchmarks</span>
        {data?.generated_at && <span className="num text-[11px] text-dim">generated {String(data.generated_at)}</span>}
        {flat.length > 0 && (
          <span className="num text-[11px] text-dim">
            · {scales.length} scales × {strategies.length} strategies
          </span>
        )}
        <Button size="xs" className="ml-auto" icon={<RefreshCw size={11} />} loading={loading} onClick={() => void load()}>
          Refresh
        </Button>
      </div>
      {error && <ErrorBanner message={error} onRetry={() => void load()} />}
      {loading && !data && <Skeleton lines={8} />}
      {!loading && data && flat.length === 0 && <Empty title="No benchmark results" hint="Run `make bench` in the backend to produce benchmarks/results/latest.json." />}
      {flat.length > 0 && (
        <>
          <div className="flex flex-wrap gap-1.5">
            {strategies.map((s) => (
              <Badge key={s} color={strategyColor(s)}>
                {s}
              </Badge>
            ))}
          </div>
          <div className="grid grid-cols-2 gap-3 xl:grid-cols-3">
            {kpis.map((k) => {
              const rows = scales.map((scale) => {
                const row: Record<string, number | string> = { scale };
                for (const f of flat) if (f.scale === scale && isNum(f.mean[k.key])) row[f.strategy] = f.mean[k.key];
                return row;
              });
              return (
                <Panel key={k.key} label={k.label} title={k.lowerBetter ? "lower is better" : "higher is better"}>
                  <ResponsiveContainer width="100%" height={170}>
                    <BarChart data={rows} margin={{ top: 4, right: 4, left: -14, bottom: 0 }} barGap={2}>
                      <CartesianGrid stroke={C.border} strokeDasharray="2 4" vertical={false} />
                      <XAxis dataKey="scale" stroke={C.dim} tickLine={false} />
                      <YAxis stroke={C.dim} tickLine={false} tickFormatter={(v: number) => axisLabel(v, k.key.includes("rate") || k.key.includes("utilization"))} width={46} />
                      <Tooltip {...TOOLTIP_STYLE} formatter={(v: unknown, name: unknown) => [isNum(v) ? k.fmt(v) : String(v), String(name)]} cursor={{ fill: "rgba(255,255,255,0.03)" }} />
                      {strategies.map((s) => (
                        <Bar key={s} dataKey={s} fill={strategyColor(s)} isAnimationActive={false} radius={[2, 2, 0, 0]} />
                      ))}
                    </BarChart>
                  </ResponsiveContainer>
                </Panel>
              );
            })}
          </div>

          <div className="grid grid-cols-[380px_1fr] gap-3">
            <Panel
              label="Normalised KPI radar"
              title="1 = best strategy per KPI"
              actions={<Select value={effectiveRadarScale} onChange={(e) => setRadarScale(e.target.value)} options={scales.map((s) => ({ value: s, label: s }))} />}
            >
              <ResponsiveContainer width="100%" height={300}>
                <RadarChart data={radarRows} outerRadius={100}>
                  <PolarGrid stroke={C.border} />
                  <PolarAngleAxis dataKey="kpi" tick={{ fill: C.muted, fontSize: 9 }} />
                  <PolarRadiusAxis domain={[0, 1]} tick={false} axisLine={false} />
                  {strategies.map((s) => (
                    <Radar key={s} name={s} dataKey={s} stroke={strategyColor(s)} fill={strategyColor(s)} fillOpacity={0.12} strokeWidth={1.5} isAnimationActive={false} />
                  ))}
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  <Tooltip {...TOOLTIP_STYLE} formatter={(v: unknown, name: unknown) => [isNum(v) ? v.toFixed(2) : String(v), String(name)]} />
                </RadarChart>
              </ResponsiveContainer>
            </Panel>
            <Panel label="Results table" title="mean ± std per scale × strategy" padded={false}>
              <div className="max-h-[420px] overflow-auto">
                <Table>
                  <thead>
                    <tr>
                      <Th>Scale</Th>
                      <Th>Strategy</Th>
                      <Th align="right">Runs</Th>
                      {kpis.map((k) => (
                        <Th key={k.key} align="right">
                          {k.label}
                        </Th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {flat.map((f) => (
                      <tr key={`${f.scale}-${f.strategy}`} className="hover:bg-panel-2">
                        <Td className="text-muted">{f.scale}</Td>
                        <Td>
                          <span className="font-semibold" style={{ color: strategyColor(f.strategy) }}>
                            {f.strategy}
                          </span>
                        </Td>
                        <Td align="right" className="text-muted">
                          {f.runs ?? "—"}
                        </Td>
                        {kpis.map((k) => {
                          const v = f.mean[k.key];
                          const s = f.std[k.key];
                          return (
                            <Td key={k.key} align="right" className="text-text">
                              {isNum(v) ? k.fmt(v) : "—"}
                              {isNum(s) && <span className="text-[10px] text-dim"> ±{k.fmt(s)}</span>}
                            </Td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </Table>
              </div>
            </Panel>
          </div>

          {summaryRows.length > 0 && (
            <Panel label="Summary table" title="as published by the benchmark runner" padded={false}>
              <div className="max-h-[360px] overflow-auto">
                <Table>
                  <thead>
                    <tr>
                      {summaryCols.map((c) => (
                        <Th key={c}>{titleCase(c)}</Th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {summaryRows.map((r, i) => (
                      <tr key={i} className="hover:bg-panel-2">
                        {summaryCols.map((c) => {
                          const v = r[c];
                          return (
                            <Td key={c} className={typeof v === "number" ? "num" : ""}>
                              {typeof v === "number" ? (Number.isInteger(v) ? num(v) : fixed(v, 3)) : v === null || v === undefined ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v)}
                            </Td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </Table>
              </div>
            </Panel>
          )}
        </>
      )}
    </div>
  );
}
