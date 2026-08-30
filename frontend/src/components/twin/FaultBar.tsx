"use client";

import { BrainCircuit, Zap } from "lucide-react";
import { useShallow } from "zustand/react/shallow";
import { Button } from "@/components/ui/Button";
import { useDecisionStore } from "@/store/decisionStore";
import { useTwinStore } from "@/store/twinStore";

export function FaultBar() {
  const { presets, fireFault, faultBusy, lastError, clearError } = useTwinStore(
    useShallow((s) => ({ presets: s.faultPresets, fireFault: s.fireFault, faultBusy: s.faultBusy, lastError: s.lastError, clearError: s.clearError })),
  );
  const { create, creating, openDrawer } = useDecisionStore(useShallow((s) => ({ create: s.create, creating: s.creating, openDrawer: s.openDrawer })));

  const decideNow = async () => {
    openDrawer();
    const d = await create({ trigger: "manual" });
    if (d) openDrawer(d.id);
  };

  return (
    <div className="flex items-center gap-2 rounded border border-border bg-bg/90 p-1.5 backdrop-blur">
      <span className="label flex items-center gap-1 px-1">
        <Zap size={11} className="text-warn" /> Faults
      </span>
      <div className="flex flex-wrap items-center gap-1">
        {presets.length === 0 && <span className="px-1 text-[11px] text-dim">no presets loaded</span>}
        {presets.map((p) => (
          <Button key={p.id} size="xs" variant="outline" title={p.description} loading={faultBusy === p.id} onClick={() => void fireFault(p.id)}>
            {p.name}
          </Button>
        ))}
      </div>
      <span className="mx-1 h-5 w-px bg-border" />
      <Button size="xs" variant="violet" icon={<BrainCircuit size={12} />} loading={creating} onClick={() => void decideNow()} title="POST /api/decisions">
        Decide now
      </Button>
      {lastError && (
        <button type="button" onClick={clearError} className="ml-1 max-w-[260px] truncate text-[11px] text-bad" title={lastError}>
          {lastError}
        </button>
      )}
    </div>
  );
}
