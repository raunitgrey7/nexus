"use client";

import { useShallow } from "zustand/react/shallow";
import { Badge } from "@/components/ui/Badge";
import { BatteryBar } from "@/components/ui/Bar";
import { Empty } from "@/components/ui/State";
import { Table, Td, Th } from "@/components/ui/Table";
import { robotColor } from "@/lib/colors";
import { useTwinStore } from "@/store/twinStore";

export function RobotsTable() {
  const { robotIds, robots, selectedRobotId, selectRobot } = useTwinStore(
    useShallow((s) => ({ robotIds: s.robotIds, robots: s.robots, selectedRobotId: s.selectedRobotId, selectRobot: s.selectRobot })),
  );
  if (robotIds.length === 0) return <Empty title="No robots" hint="The world has not been loaded yet." />;
  return (
    <div className="h-full overflow-auto">
      <Table>
        <thead>
          <tr>
            <Th>Robot</Th>
            <Th>Status</Th>
            <Th>Battery</Th>
            <Th>Task</Th>
            <Th>Zone</Th>
            <Th align="right">Load</Th>
          </tr>
        </thead>
        <tbody>
          {robotIds.map((id) => {
            const r = robots[id];
            if (!r) return null;
            const selected = selectedRobotId === id;
            return (
              <tr
                key={id}
                onClick={() => selectRobot(selected ? null : id)}
                className={`cursor-pointer transition-colors hover:bg-panel-2 ${selected ? "bg-accent/10" : ""}`}
              >
                <Td className="num font-semibold text-text">{id}</Td>
                <Td>
                  <Badge color={robotColor(r.status)} pulse={r.status === "failed"}>
                    {r.status}
                  </Badge>
                </Td>
                <Td>
                  <BatteryBar value={r.battery} />
                </Td>
                <Td className="num text-muted">{r.task_id ?? <span className="text-dim">—</span>}</Td>
                <Td className="num text-muted">{r.zone_id}</Td>
                <Td align="right" className="text-muted">
                  {r.load}/{r.capacity}
                </Td>
              </tr>
            );
          })}
        </tbody>
      </Table>
    </div>
  );
}
