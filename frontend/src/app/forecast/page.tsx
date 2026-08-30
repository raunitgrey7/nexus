"use client";

import { useEffect, useMemo } from "react";
import { Anchor, BatteryCharging, Bot, Boxes, Map, RefreshCw, TrendingDown, TrendingUp, Minus, User } from "lucide-react";
import { Area, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useShallow } from "zustand/react/shallow";
import { TOOLTIP_STYLE } from "@/components/decisions/TimelineCompareChart";
import { Badge } from "@/components/ui/Badge";
import { Bar, BatteryBar } from "@/components/ui/Bar";
import { Button } from "@/components/ui/Button";
import { Panel } from "@/components/ui/Panel";
import { Select } from "@/components/ui/Select";
import { Empty, ErrorBanner, Skeleton } from "@/components/ui/State";
import { Table, Td, Th } from "@/components/ui/Table";
import { C, congestionColor, lmhColor, robotColor } from "@/lib/colors";
import { fixed, num, pct, simTimeLabel } from "@/lib/format";
import type { BottleneckKind } from "@/lib/types";
import { useForecastStore } from "@/store/forecastStore";

const KIND_ICON: Record<BottleneckKind, typeof Map> = { zone: Map, dock: Anchor, charger: BatteryCharging, robot: Bot, inventory: Boxes, worker: User, demand: TrendingUp };

function Gauge({ value, label }: { value: number; label: string }) {
  const v = Math.max(0, Math.min(1.25, value));
  const r = 44;
  const cx = 60;
  const cy = 58;
  const start = Math.PI;
  const end = Math.PI + Math.PI * Math.min(1, v);
  const arc = (a0: number, a1: number) => {
    const x0 = cx + r * Math.cos(a0);
    const y0 = cy + r * Math.sin(a0);
    const x1 = cx + r * Math.cos(a1);
    const y1 = cy + r * Math.sin(a1);
    return `M ${x0} ${y0} A ${r} ${r} 0 ${a1 - a0 > Math.PI ? 1 : 0} 1 ${x1} ${y1}`;
  };
  const color = v >= 1 ? C.bad : v >= 0.85 ? C.warn : C.accent;
  return (
    <div className="flex flex-col items-center">
      <svg viewBox="0 0 120 66" className="w-40">
        <path d={arc(start, 2 * Math.PI)} stroke="#1a222c" strokeWidth={9} fill="none" strokeLinecap="round" />
        {v > 0 && <path d={arc(start, end)} stroke={color} strokeWidth={9} fill="none" strokeLinecap="round" style={{ filter: `drop-shadow(0 0 4px ${color}88)` }} />}
        <text x={cx} y={cy - 4} textAnchor="middle" fill={color} fontSize={18} fontFamily="var(--font-geist-mono)" fontWeight={600}>
          {Math.round(value * 100)}%
        </text>
      </svg>
      <span className="label -mt-1">{label}</span>
    </div>
  );
}

export default function ForecastPage() {
  const { forecast, load, loading, error, horizonMin, updatedAt } = useForecastStore(
    useShallow((s) => ({ forecast: s.forecast, load: s.load, loading: s.loading, error: s.error, horizonMin: s.horizonMin, updatedAt: s.updatedAt })),
  );
  useEffect(() => {
    if (!forecast) void load();
  }, [forecast, load]);

  const demandRows = useMemo(
    () =>
      forecast?.demand.per_bucket.map((b) => ({
        label: `+${b.end_min}m`,
        expected: b.expected_orders,
        band: [b.lower, b.upper] as [number, number],
        lower: b.lower,
        upper: b.upper,
      })) ?? [],
    [forecast],
  );

  const d = forecast?.demand;
  const TrendIcon = d?.trend === "rising" ? TrendingUp : d?.trend === "falling" ? TrendingDown : Minus;
  const trendColor = d?.trend === "rising" ? C.warn : d?.trend === "falling" ? C.good : C.muted;

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 overflow-y-auto p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="label">Forecast</span>
        {forecast && (
          <span className="num text-[11px] text-dim">
            generated t={forecast.generated_tick} · {simTimeLabel(forecast.sim_time)}
            {updatedAt && ` · refreshed ${new Date(updatedAt).toLocaleTimeString()}`}
          </span>
        )}
        <div className="ml-auto flex items-center gap-2">
          <Select label="Horizon" value={String(horizonMin)} onChange={(e) => void load(Number(e.target.value))} options={[30, 60, 90, 120, 240].map((h) => ({ value: String(h), label: `${h} min` }))} />
          <Button size="xs" icon={<RefreshCw size={11} />} loading={loading} onClick={() => void load()}>
            Refresh
          </Button>
        </div>
      </div>
      {error && <ErrorBanner message={error} onRetry={() => void load()} />}
      {!forecast && !error && <Skeleton lines={8} />}
      {forecast && d && (
        <>
          {forecast.summary && <p className="rounded border border-border bg-panel px-3 py-2 text-xs text-muted">{forecast.summary}</p>}
          <div className="grid grid-cols-[1fr_280px] gap-3">
            <Panel label="Demand" title={`${d.method} · confidence ${pct(d.confidence, 0)}`}>
              <ResponsiveContainer width="100%" height={220}>
                <ComposedChart data={demandRows} margin={{ top: 8, right: 8, left: -14, bottom: 0 }}>
                  <defs>
                    <linearGradient id="band" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={C.accent} stopOpacity={0.28} />
                      <stop offset="100%" stopColor={C.accent} stopOpacity={0.06} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke={C.border} strokeDasharray="2 4" />
                  <XAxis dataKey="label" stroke={C.dim} tickLine={false} />
                  <YAxis stroke={C.dim} tickLine={false} />
                  <Tooltip
                    {...TOOLTIP_STYLE}
                    formatter={(v: unknown, name: unknown) => {
                      if (Array.isArray(v)) return [`${fixed(Number(v[0]))} – ${fixed(Number(v[1]))}`, "band"];
                      return [typeof v === "number" ? fixed(v) : String(v), String(name)];
                    }}
                  />
                  <Area type="monotone" dataKey="band" name="band" stroke="none" fill="url(#band)" isAnimationActive={false} />
                  <Line type="monotone" dataKey="expected" name="expected orders" stroke={C.accent} strokeWidth={2} dot={{ r: 2, fill: C.accent }} isAnimationActive={false} />
                </ComposedChart>
              </ResponsiveContainer>
            </Panel>
            <Panel label="Capacity">
              <div className="flex flex-col items-center gap-2">
                <Badge color={trendColor} variant="outline" className="self-start">
                  <TrendIcon size={11} /> {d.trend}
                </Badge>
                <Gauge value={d.projected_utilization} label="Projected utilization" />
                <div className="grid w-full grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
                  <span className="text-muted">Now</span>
                  <span className="num text-right text-text">{num(d.current_rate_per_hour)} /h</span>
                  <span className="text-muted">Forecast</span>
                  <span className="num text-right text-text">{num(d.forecast_rate_per_hour)} /h</span>
                  <span className="text-muted">Capacity</span>
                  <span className="num text-right text-text">{num(d.capacity_per_hour)} /h</span>
                  <span className="text-muted">Expected orders</span>
                  <span className="num text-right text-text">{num(d.expected_orders)}</span>
                </div>
                <Bar value={d.forecast_rate_per_hour / Math.max(1, d.capacity_per_hour)} color={d.forecast_rate_per_hour > d.capacity_per_hour ? C.bad : C.accent} marker={1} title="forecast rate vs capacity" />
              </div>
            </Panel>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <Panel label="Battery" title={`${forecast.battery.length} robots`} padded={false}>
              {forecast.battery.length === 0 ? (
                <Empty title="No battery forecast" />
              ) : (
                <div className="max-h-[360px] overflow-auto">
                  <Table>
                    <thead>
                      <tr>
                        <Th>Robot</Th>
                        <Th>Battery</Th>
                        <Th>Status</Th>
                        <Th align="right">Exhaustion</Th>
                        <Th align="right">Charger ETA</Th>
                        <Th>Risk</Th>
                        <Th>Recommendation</Th>
                      </tr>
                    </thead>
                    <tbody>
                      {forecast.battery.map((b) => (
                        <tr key={b.robot_id} className="hover:bg-panel-2">
                          <Td className="num font-semibold text-text">{b.robot_id}</Td>
                          <Td>
                            <BatteryBar value={b.battery} />
                          </Td>
                          <Td>
                            <Badge color={robotColor(b.status)}>{b.status}</Badge>
                          </Td>
                          <Td align="right" className={b.predicted_exhaustion_min !== null && b.predicted_exhaustion_min < 20 ? "text-bad" : "text-muted"}>
                            {b.predicted_exhaustion_min === null ? "—" : `${fixed(b.predicted_exhaustion_min, 0)} min`}
                          </Td>
                          <Td align="right" className="text-muted">
                            {b.charger_eta_min === null ? "—" : `${fixed(b.charger_eta_min, 0)} min`}
                          </Td>
                          <Td>
                            <Badge color={lmhColor(b.risk)}>{b.risk}</Badge>
                          </Td>
                          <Td className="max-w-[220px] truncate text-[11px] text-muted" title={b.recommendation}>
                            {b.recommendation}
                          </Td>
                        </tr>
                      ))}
                    </tbody>
                  </Table>
                </div>
              )}
            </Panel>
            <Panel label="Congestion" title="now vs projected · capacity marker">
              {forecast.congestion.length === 0 ? (
                <Empty title="No congestion forecast" />
              ) : (
                <ul className="flex max-h-[360px] flex-col gap-2 overflow-auto pr-1">
                  {forecast.congestion.map((z) => {
                    const max = Math.max(z.capacity * 1.5, z.projected_robots, z.robots_now, 1);
                    return (
                      <li key={z.zone_id} className="rounded border border-border/70 bg-bg/40 p-2">
                        <div className="flex items-center gap-2">
                          <span className="num text-xs font-semibold text-text">{z.zone_id}</span>
                          <span className="text-[10px] text-dim">{z.zone_name}</span>
                          <Badge color={lmhColor(z.risk)} className="ml-auto">
                            {z.risk}
                          </Badge>
                          <span className="num text-[10px] text-dim">ETA {fixed(z.eta_min, 0)} min</span>
                        </div>
                        <div className="mt-1.5 grid grid-cols-[52px_1fr_44px] items-center gap-2 text-[10px]">
                          <span className="text-muted">now</span>
                          <Bar value={z.robots_now / max} color={congestionColor(z.robots_now / Math.max(1, z.capacity))} marker={z.capacity / max} />
                          <span className="num text-right text-text">{z.robots_now}</span>
                          <span className="text-muted">projected</span>
                          <Bar value={z.projected_robots / max} color={congestionColor(z.projected_robots / Math.max(1, z.capacity))} marker={z.capacity / max} />
                          <span className="num text-right text-text">
                            {fixed(z.projected_robots)}
                            <span className={z.projected_change_pct > 0 ? "text-warn" : "text-good"}> {z.projected_change_pct > 0 ? "+" : ""}{Math.round(z.projected_change_pct)}%</span>
                          </span>
                        </div>
                        {z.drivers.length > 0 && <div className="mt-1 text-[10px] text-dim">{z.drivers.join(" · ")}</div>}
                      </li>
                    );
                  })}
                </ul>
              )}
            </Panel>
          </div>

          <Panel label="Bottlenecks" title={`${forecast.bottlenecks.length} detected`}>
            {forecast.bottlenecks.length === 0 ? (
              <Empty title="No bottlenecks detected" hint="The forecaster found no capacity, battery or demand risks in this horizon." />
            ) : (
              <ul className="grid grid-cols-2 gap-2">
                {forecast.bottlenecks
                  .slice()
                  .sort((a, b) => b.severity - a.severity)
                  .map((b, i) => {
                    const Icon = KIND_ICON[b.kind] ?? Map;
                    const color = b.severity >= 0.66 ? C.bad : b.severity >= 0.33 ? C.warn : C.accent;
                    return (
                      <li key={i} className="flex gap-3 rounded border border-border bg-bg/40 p-2.5">
                        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded border" style={{ borderColor: `${color}66`, color }}>
                          <Icon size={15} />
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <Badge color={color}>{b.kind}</Badge>
                            <span className="num text-[11px] text-text">{b.entity_id}</span>
                            <div className="ml-auto flex items-center gap-1.5">
                              <span className="label">severity</span>
                              <Bar value={b.severity} color={color} className="w-16" />
                              <span className="num text-[10px]" style={{ color }}>
                                {Math.round(b.severity * 100)}
                              </span>
                            </div>
                          </div>
                          <div className="mt-1 text-xs text-text/90">{b.message}</div>
                          <div className="mt-0.5 text-[11px] text-muted">→ {b.recommendation}</div>
                        </div>
                      </li>
                    );
                  })}
              </ul>
            )}
          </Panel>
        </>
      )}
    </div>
  );
}
