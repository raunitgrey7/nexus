"use client";

import { useMemo } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { C } from "@/lib/colors";
import type { TimelinePoint } from "@/lib/types";
import { Empty } from "@/components/ui/State";

export interface Series {
  id: string;
  name: string;
  color: string;
  points: TimelinePoint[];
  dashed?: boolean;
}

interface Row {
  min: number;
  [k: string]: number | undefined;
}

export const TOOLTIP_STYLE = {
  contentStyle: { background: "#11161d", border: "1px solid #1f2933", borderRadius: 6, fontSize: 11, fontFamily: "var(--font-geist-mono)" },
  labelStyle: { color: "#8b98a5" },
  itemStyle: { padding: 0 },
} as const;

function fmtPct(v: unknown): string {
  return typeof v === "number" ? `${(v * 100).toFixed(1)}%` : String(v);
}

/** Overlay of `breach_projected` (left axis) and `open` orders (right axis, dashed) for several series. */
export function TimelineCompareChart({ series, height = 220, showOpen = true }: { series: Series[]; height?: number; showOpen?: boolean }) {
  const rows = useMemo(() => {
    const byMin = new Map<number, Row>();
    for (const s of series) {
      const t0 = s.points[0]?.tick ?? 0;
      for (const p of s.points) {
        const min = Math.round((p.tick - t0) / 60);
        const row = byMin.get(min) ?? { min };
        row[`${s.id}_breach`] = p.breach_projected;
        row[`${s.id}_open`] = p.open;
        byMin.set(min, row);
      }
    }
    return [...byMin.values()].sort((a, b) => a.min - b.min);
  }, [series]);

  if (series.length === 0 || rows.length === 0) return <Empty title="No simulation timeline" hint="Timelines appear once a plan has been simulated." />;

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={rows} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
        <CartesianGrid stroke={C.border} strokeDasharray="2 4" />
        <XAxis dataKey="min" tickFormatter={(v: number) => `${v}m`} stroke={C.dim} tickLine={false} />
        <YAxis yAxisId="breach" tickFormatter={(v: number) => `${Math.round(v * 100)}%`} stroke={C.dim} tickLine={false} domain={[0, (max: number) => Math.max(0.05, Math.ceil(max * 20) / 20)]} />
        {showOpen && <YAxis yAxisId="open" orientation="right" stroke={C.dim} tickLine={false} width={40} />}
        <Tooltip {...TOOLTIP_STYLE} formatter={(v: unknown, name: unknown) => [String(name).includes("open") ? String(v) : fmtPct(v), String(name)]} labelFormatter={(v) => `+${v} min`} />
        <Legend iconType="plainline" wrapperStyle={{ fontSize: 11 }} />
        {series.map((s) => (
          <Line
            key={`${s.id}_breach`}
            yAxisId="breach"
            type="monotone"
            dataKey={`${s.id}_breach`}
            name={`${s.name} · SLA breach`}
            stroke={s.color}
            strokeWidth={s.dashed ? 1.5 : 2}
            strokeDasharray={s.dashed ? "4 3" : undefined}
            dot={false}
            isAnimationActive={false}
          />
        ))}
        {showOpen &&
          series.map((s) => (
            <Line
              key={`${s.id}_open`}
              yAxisId="open"
              type="monotone"
              dataKey={`${s.id}_open`}
              name={`${s.name} · open`}
              stroke={s.color}
              strokeWidth={1}
              strokeDasharray="2 3"
              strokeOpacity={0.6}
              dot={false}
              isAnimationActive={false}
            />
          ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
