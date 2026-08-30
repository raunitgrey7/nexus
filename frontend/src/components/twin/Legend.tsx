import { ROBOT_COLORS } from "@/lib/colors";
import type { RobotStatus } from "@/lib/types";

const SHOWN: RobotStatus[] = ["idle", "moving", "picking", "delivering", "charging", "waiting", "failed"];

export function Legend() {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded border border-border bg-bg/80 px-2 py-1 backdrop-blur">
      {SHOWN.map((s) => (
        <span key={s} className="flex items-center gap-1 text-[10px] text-muted">
          <span className="h-2 w-2 rounded-full" style={{ background: ROBOT_COLORS[s], boxShadow: `0 0 5px ${ROBOT_COLORS[s]}` }} />
          {s}
        </span>
      ))}
      <span className="flex items-center gap-1 text-[10px] text-muted">
        <span className="h-2 w-2 rounded-sm bg-warn" /> dock
      </span>
      <span className="flex items-center gap-1 text-[10px] text-muted">
        <span className="h-2 w-2 rounded-sm bg-good" /> charger
      </span>
      <span className="flex items-center gap-1 text-[10px] text-muted">
        <span className="h-2 w-2 rounded-sm bg-bad" /> blocked
      </span>
    </div>
  );
}
