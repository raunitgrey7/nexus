"use client";

import { useEffect, useMemo } from "react";
import { useShallow } from "zustand/react/shallow";
import { Badge } from "@/components/ui/Badge";
import { Empty } from "@/components/ui/State";
import { Table, Td, Th } from "@/components/ui/Table";
import { C, PRIORITY_COLORS } from "@/lib/colors";
import { ticksToCountdown } from "@/lib/format";
import { useTwinStore } from "@/store/twinStore";

const STATUS_COLORS: Record<string, string> = {
  pending: C.muted,
  assigned: C.blue,
  in_progress: C.accent,
  delivered: C.good,
  cancelled: C.dim,
};

export function OrdersTable() {
  const { orders, tick, tickSeconds, loadWorld, world } = useTwinStore(
    useShallow((s) => ({ orders: s.world?.orders ?? [], tick: s.tick, tickSeconds: s.tickSeconds, loadWorld: s.loadWorld, world: s.world })),
  );
  // orders are not part of tick frames: refresh the light world payload periodically while the tab is visible
  useEffect(() => {
    const t = setInterval(() => void loadWorld(false), 4000);
    return () => clearInterval(t);
  }, [loadWorld]);

  const list = useMemo(
    () => orders.filter((o) => o.status === "pending" || o.status === "assigned" || o.status === "in_progress").sort((a, b) => a.deadline_tick - b.deadline_tick),
    [orders],
  );
  if (!world) return <Empty title="No world loaded" />;
  if (list.length === 0) return <Empty title="No open orders" hint="Orders arrive as the simulation runs." />;
  return (
    <div className="h-full overflow-auto">
      <Table>
        <thead>
          <tr>
            <Th>Order</Th>
            <Th>Prio</Th>
            <Th>Status</Th>
            <Th align="right">Lines</Th>
            <Th>Robot</Th>
            <Th align="right">Deadline</Th>
          </tr>
        </thead>
        <tbody>
          {list.map((o) => {
            const remaining = o.deadline_tick - tick;
            const overdue = remaining < 0;
            const soon = !overdue && remaining < 120;
            return (
              <tr key={o.id} className="hover:bg-panel-2">
                <Td className="num text-text">{o.id}</Td>
                <Td>
                  <Badge color={PRIORITY_COLORS[o.priority_name] ?? C.muted}>{o.priority_name}</Badge>
                </Td>
                <Td>
                  <Badge color={STATUS_COLORS[o.status] ?? C.muted}>{o.status.replace("_", " ")}</Badge>
                </Td>
                <Td align="right" className="text-muted">
                  {o.lines.filter((l) => l.picked).length}/{o.lines.length}
                </Td>
                <Td className="num text-muted">{o.robot_id ?? <span className="text-dim">—</span>}</Td>
                <Td align="right" className={overdue ? "text-bad" : soon ? "text-warn" : "text-muted"}>
                  {ticksToCountdown(remaining, tickSeconds)}
                </Td>
              </tr>
            );
          })}
        </tbody>
      </Table>
    </div>
  );
}
