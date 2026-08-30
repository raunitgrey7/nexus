"use client";

import dynamic from "next/dynamic";
import { useEffect } from "react";
import { Box, Maximize2, Square } from "lucide-react";
import { useShallow } from "zustand/react/shallow";
import { DecisionDrawer } from "@/components/decisions/DecisionDrawer";
import { FaultBar } from "@/components/twin/FaultBar";
import { KpiBar } from "@/components/twin/KpiBar";
import { Legend } from "@/components/twin/Legend";
import { RightPanel } from "@/components/twin/RightPanel";
import { SimControls } from "@/components/twin/SimControls";
import { Button } from "@/components/ui/Button";
import { ErrorBanner, Loading } from "@/components/ui/State";
import { useTwinStore } from "@/store/twinStore";

const TwinView3D = dynamic(() => import("@/components/twin/TwinView3D"), {
  ssr: false,
  loading: () => <Loading label="Loading 3D twin" />,
});
const TwinView2D = dynamic(() => import("@/components/twin/TwinView2D"), { ssr: false, loading: () => <Loading label="Loading 2D twin" /> });

export default function LivePage() {
  const { world, worldLoading, worldError, loadWorld, viewMode, setViewMode, requestFit, status, control, selectedRobotId, robots } = useTwinStore(
    useShallow((s) => ({
      world: s.world,
      worldLoading: s.worldLoading,
      worldError: s.worldError,
      loadWorld: s.loadWorld,
      viewMode: s.viewMode,
      setViewMode: s.setViewMode,
      requestFit: s.requestFit,
      status: s.status,
      control: s.control,
      selectedRobotId: s.selectedRobotId,
      robots: s.robots,
    })),
  );

  // space = play / pause
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.code !== "Space") return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
      e.preventDefault();
      const running = useTwinStore.getState().status?.running ?? false;
      void control({ action: running ? "pause" : "start" });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [control]);

  const selected = selectedRobotId ? robots[selectedRobotId] : null;

  return (
    <div className="relative flex h-full min-h-0">
      <div className="flex min-w-0 flex-1 flex-col">
        <KpiBar />
        <div className="relative min-h-0 flex-1 bg-bg">
          {worldError && !world && (
            <div className="absolute inset-x-0 top-3 z-20 mx-auto max-w-lg">
              <ErrorBanner message={worldError} onRetry={() => void loadWorld(true)} />
            </div>
          )}
          {!world && worldLoading && <Loading label="Loading world" />}
          {world?.grid && (viewMode === "3d" ? <TwinView3D /> : <TwinView2D world={world} live />)}

          {/* overlays */}
          <div className="pointer-events-none absolute inset-x-3 top-3 z-10 flex items-start justify-between gap-2">
            <div className="pointer-events-auto">
              <SimControls />
            </div>
            <div className="pointer-events-auto flex items-center gap-1 rounded border border-border bg-bg/80 p-1 backdrop-blur">
              <Button size="xs" variant="ghost" active={viewMode === "3d"} icon={<Box size={12} />} onClick={() => setViewMode("3d")}>
                3D
              </Button>
              <Button size="xs" variant="ghost" active={viewMode === "2d"} icon={<Square size={12} />} onClick={() => setViewMode("2d")}>
                2D
              </Button>
              {viewMode === "3d" && <Button size="xs" variant="ghost" icon={<Maximize2 size={12} />} onClick={requestFit} title="Fit to world" />}
            </div>
          </div>
          <div className="pointer-events-none absolute inset-x-3 bottom-3 z-10 flex items-end justify-between gap-2">
            <div className="pointer-events-auto">
              <FaultBar />
            </div>
            <div className="pointer-events-auto flex flex-col items-end gap-1">
              {selected && (
                <div className="fade-in rounded border border-border bg-bg/85 px-2 py-1 text-[11px] backdrop-blur">
                  <span className="num font-semibold text-text">{selected.id}</span> <span className="text-muted">{selected.status}</span>{" "}
                  <span className="num text-dim">
                    [{selected.cell[0]},{selected.cell[1]}] · {selected.zone_id} · {Math.round(selected.battery)}%
                  </span>
                  {selected.task_id && <span className="num text-dim"> · {selected.task_id}</span>}
                  {selected.failure_cause && <span className="text-bad"> · {selected.failure_cause}</span>}
                </div>
              )}
              <Legend />
            </div>
          </div>
          {status && !status.running && world && (
            <div className="pointer-events-none absolute left-1/2 top-16 z-10 -translate-x-1/2 rounded border border-warn/40 bg-bg/80 px-2 py-0.5 text-[10px] uppercase tracking-widest text-warn">
              paused
            </div>
          )}
        </div>
      </div>
      <RightPanel />
      <DecisionDrawer />
    </div>
  );
}
