"use client";

import { useShallow } from "zustand/react/shallow";
import { RiskBadge } from "@/components/ui/Badge";
import { C, systemRisk } from "@/lib/colors";
import { fixed, num, pct } from "@/lib/format";
import { useTwinStore } from "@/store/twinStore";

function Tile({ label, value, sub, color, wide }: { label: string; value: string; sub?: string; color?: string; wide?: boolean }) {
  return (
    <div className={`flex min-w-0 flex-col justify-center border-r border-border px-3 py-1 last:border-r-0 ${wide ? "min-w-[150px]" : "min-w-[108px]"}`}>
      <span className="label truncate">{label}</span>
      <span className="flex items-baseline gap-1.5">
        <span className="num text-[17px] leading-tight text-text" style={{ color }}>
          {value}
        </span>
        {sub && <span className="num text-[10px] text-dim">{sub}</span>}
      </span>
    </div>
  );
}

export function KpiBar() {
  const { kpis, robotIds, robots, worldLoading } = useTwinStore(
    useShallow((s) => ({ kpis: s.kpis, robotIds: s.robotIds, robots: s.robots, worldLoading: s.worldLoading })),
  );
  const failed = robotIds.filter((id) => robots[id]?.status === "failed" || robots[id]?.status === "maintenance").length;
  const total = kpis?.robots_total ?? robotIds.length;
  const operational = kpis?.robots_operational ?? total - failed;
  const breach = kpis?.sla_breach_rate_projected;
  const risk = kpis ? systemRisk(breach, kpis.congestion_index, failed) : null;
  const breachColor = breach === undefined ? undefined : breach >= 0.12 ? C.bad : breach >= 0.06 ? C.warn : C.good;

  if (!kpis && worldLoading) {
    return <div className="flex h-12 items-center border-b border-border bg-panel px-3 text-xs text-muted">Loading KPIs…</div>;
  }

  return (
    <div className="flex h-12 items-stretch overflow-x-auto border-b border-border bg-panel">
      <Tile label="Active orders" value={num(kpis?.orders_open)} sub={kpis ? `${kpis.orders_pending} pending` : undefined} />
      <Tile
        label="Robots operational"
        value={`${operational}/${total}`}
        sub={failed ? `${failed} down` : undefined}
        color={failed ? C.warn : undefined}
      />
      <Tile label="Predicted SLA breach" value={pct(breach)} sub={kpis ? `now ${pct(kpis.sla_breach_rate)}` : undefined} color={breachColor} wide />
      <Tile label="Throughput / h" value={num(kpis?.throughput_per_hour)} sub={kpis ? `${kpis.orders_delivered} done` : undefined} />
      <Tile label="Avg fulfilment" value={kpis ? `${fixed(kpis.avg_fulfillment_min)}m` : "—"} sub={kpis ? `p95 ${fixed(kpis.p95_fulfillment_min)}m` : undefined} />
      <Tile label="Utilization" value={pct(kpis?.robot_utilization, 0)} />
      <Tile
        label="Congestion"
        value={fixed(kpis?.congestion_index, 2)}
        color={kpis && kpis.congestion_index >= 1 ? C.bad : kpis && kpis.congestion_index >= 0.4 ? C.warn : undefined}
      />
      <div className="flex min-w-[120px] flex-col justify-center px-3 py-1">
        <span className="label">System risk</span>
        <span className="mt-0.5">
          <RiskBadge level={risk} />
        </span>
      </div>
    </div>
  );
}
