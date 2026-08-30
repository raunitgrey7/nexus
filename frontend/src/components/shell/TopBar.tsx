"use client";

import { usePathname } from "next/navigation";
import { useShallow } from "zustand/react/shallow";
import { Badge, Dot } from "@/components/ui/Badge";
import { C } from "@/lib/colors";
import { simTimeLabel } from "@/lib/format";
import type { ConnectionState } from "@/lib/types";
import { useTwinStore } from "@/store/twinStore";
import { pageTitle } from "./Rail";

const CONN: Record<ConnectionState, { color: string; label: string; pulse?: boolean }> = {
  idle: { color: C.dim, label: "idle" },
  connecting: { color: C.warn, label: "connecting", pulse: true },
  open: { color: C.good, label: "live" },
  reconnecting: { color: C.warn, label: "reconnecting", pulse: true },
  closed: { color: C.bad, label: "offline" },
  mock: { color: C.violet, label: "mock" },
};

export function TopBar() {
  const pathname = usePathname();
  const { connection, simTime, tick, status } = useTwinStore(
    useShallow((s) => ({ connection: s.connection, simTime: s.simTime, tick: s.tick, status: s.status })),
  );
  const conn = CONN[connection];
  return (
    <header className="col-span-2 flex items-center gap-4 border-b border-border bg-panel px-3">
      <div className="flex items-center gap-2">
        <span className="inline-block h-2.5 w-2.5 rounded-sm bg-accent shadow-[0_0_10px_#22d3ee]" />
        <span className="text-sm font-bold tracking-[0.22em] text-text">NEXUS</span>
        <span className="hidden text-[10px] uppercase tracking-widest text-dim lg:inline">Digital twin · autonomous ops</span>
      </div>
      <span className="text-dim">/</span>
      <span className="text-xs font-semibold text-muted">{pageTitle(pathname)}</span>

      <div className="ml-auto flex items-center gap-3">
        <div className="flex items-center gap-2 rounded border border-border bg-bg px-2 py-1">
          <span className="label">Sim</span>
          <span className="num text-xs text-text">{simTimeLabel(simTime)}</span>
          <span className="num text-[10px] text-dim">t={tick}</span>
          {status && (
            <span className={`num text-[10px] ${status.running ? "text-good" : "text-warn"}`}>
              {status.running ? `▶ ${status.ticks_per_second}×` : "❚❚ paused"}
            </span>
          )}
        </div>
        <div className="hidden items-center gap-1.5 xl:flex">
          {status && (
            <>
              <Badge color={C.muted}>{status.scale}</Badge>
              <Badge color={C.accent}>{status.strategy}</Badge>
              <Badge color={C.muted} mono>
                seed {status.seed}
              </Badge>
              {status.autopilot && <Badge color={C.violet}>autopilot</Badge>}
              <Badge color={status.llm.available ? C.violet : C.dim} title={`${status.llm.model} @ ${status.llm.url}`}>
                LLM {status.llm.available ? "on" : "off"}
              </Badge>
            </>
          )}
        </div>
        <div className="flex items-center gap-1.5" title={`WebSocket: ${conn.label}`}>
          <Dot color={conn.color} pulse={conn.pulse} />
          <span className="text-[10px] uppercase tracking-wider text-muted">{conn.label}</span>
        </div>
      </div>
    </header>
  );
}
