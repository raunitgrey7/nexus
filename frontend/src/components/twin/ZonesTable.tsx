"use client";

import { useShallow } from "zustand/react/shallow";
import { Badge } from "@/components/ui/Badge";
import { Bar } from "@/components/ui/Bar";
import { Empty } from "@/components/ui/State";
import { Table, Td, Th } from "@/components/ui/Table";
import { C, congestionColor } from "@/lib/colors";
import { useTwinStore } from "@/store/twinStore";

const ORDER: Record<string, number> = { storage: 0, corridor: 1, dock: 2, charging: 3, staging: 4 };

export function ZonesTable() {
  const { zones, occupancy, closedZones } = useTwinStore(
    useShallow((s) => ({ zones: s.world?.zones ?? [], occupancy: s.zoneOccupancy, closedZones: s.world?.grid?.closed_zones ?? [] })),
  );
  if (zones.length === 0) return <Empty title="No zones" />;
  const sorted = zones.slice().sort((a, b) => (ORDER[a.kind] ?? 9) - (ORDER[b.kind] ?? 9) || a.id.localeCompare(b.id, undefined, { numeric: true }));
  return (
    <div className="h-full overflow-auto">
      <Table>
        <thead>
          <tr>
            <Th>Zone</Th>
            <Th>Kind</Th>
            <Th>Occupancy / capacity</Th>
            <Th align="right">Load</Th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((z) => {
            const occ = occupancy[z.id] ?? 0;
            const ratio = z.capacity > 0 ? occ / z.capacity : 0;
            const closed = z.closed || closedZones.includes(z.id);
            return (
              <tr key={z.id} className="hover:bg-panel-2">
                <Td>
                  <span className="num font-semibold text-text">{z.id}</span>
                  <span className="ml-1.5 text-[10px] text-dim">{z.name}</span>
                  {closed && (
                    <Badge color={C.bad} className="ml-1.5">
                      closed
                    </Badge>
                  )}
                </Td>
                <Td className="text-muted">{z.kind}</Td>
                <Td>
                  <div className="flex items-center gap-2">
                    <Bar value={Math.min(1, ratio)} color={congestionColor(ratio)} className="w-24" />
                    <span className="num text-[11px] text-muted">
                      {occ}/{z.capacity}
                    </span>
                  </div>
                </Td>
                <Td align="right" style={{ color: congestionColor(ratio) }}>
                  {Math.round(ratio * 100)}%
                </Td>
              </tr>
            );
          })}
        </tbody>
      </Table>
    </div>
  );
}
