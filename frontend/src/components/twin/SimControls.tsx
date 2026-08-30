"use client";

import { useEffect, useState } from "react";
import { Pause, Play, RotateCcw, StepForward } from "lucide-react";
import { useShallow } from "zustand/react/shallow";
import { Button } from "@/components/ui/Button";
import { Input, Select, Toggle } from "@/components/ui/Select";
import { simTimeLabel } from "@/lib/format";
import { useTwinStore } from "@/store/twinStore";

const SPEEDS = [1, 2, 5, 10, 20, 50, 100, 200, 500];
const SCALES = ["tiny", "small", "medium", "large"];
const FALLBACK_STRATEGIES = ["baseline", "optimized", "ai_planner", "nexus_full"];

export function SimControls({ compact = false }: { compact?: boolean }) {
  const { status, control, controlBusy, strategies, simTime, tick } = useTwinStore(
    useShallow((s) => ({
      status: s.status,
      control: s.control,
      controlBusy: s.controlBusy,
      strategies: s.strategies,
      simTime: s.simTime,
      tick: s.tick,
    })),
  );
  const [scale, setScale] = useState("small");
  const [seed, setSeed] = useState("42");
  const [strategy, setStrategy] = useState("nexus_full");
  const [resetOpen, setResetOpen] = useState(false);

  useEffect(() => {
    if (status) {
      setScale(status.scale);
      setSeed(String(status.seed));
      setStrategy(status.strategy);
    }
  }, [status?.scale, status?.seed, status?.strategy]); // eslint-disable-line react-hooks/exhaustive-deps

  const running = status?.running ?? false;
  const tps = status?.ticks_per_second ?? 10;
  const speedIdx = Math.max(
    0,
    SPEEDS.reduce((best, v, i) => (Math.abs(v - tps) < Math.abs(SPEEDS[best] - tps) ? i : best), 0),
  );
  const strategyOptions = (strategies.length ? strategies.map((s) => s.name) : FALLBACK_STRATEGIES).map((n) => ({ value: n, label: n }));

  return (
    <div className="flex flex-wrap items-center gap-2">
      <div className="flex items-center gap-1 rounded border border-border bg-bg/80 p-1">
        <Button
          size="xs"
          variant={running ? "outline" : "primary"}
          icon={running ? <Pause size={12} /> : <Play size={12} />}
          onClick={() => void control({ action: running ? "pause" : "start" })}
          disabled={controlBusy}
          title="Space"
        >
          {running ? "Pause" : "Play"}
        </Button>
        <Button size="xs" icon={<StepForward size={12} />} onClick={() => void control({ action: "step", ticks: 10 })} disabled={controlBusy || running} title="Advance 10 ticks">
          Step
        </Button>
        <div className="mx-1 flex items-center gap-1.5" title="Ticks per second">
          <span className="label">Speed</span>
          <input
            type="range"
            min={0}
            max={SPEEDS.length - 1}
            step={1}
            value={speedIdx}
            onChange={(e) => void control({ action: "speed", ticks_per_second: SPEEDS[Number(e.target.value)] })}
            className="w-20"
          />
          <span className="num w-9 text-[11px] text-accent">{tps}×</span>
        </div>
        <Toggle
          checked={status?.autopilot ?? false}
          disabled={controlBusy}
          label="Autopilot"
          onChange={(v) => void control({ action: running ? "start" : "pause", autopilot: v })}
        />
        <Button size="xs" variant="ghost" icon={<RotateCcw size={12} />} onClick={() => setResetOpen((o) => !o)} active={resetOpen}>
          Reset
        </Button>
      </div>
      {!compact && (
        <div className="num rounded border border-border bg-bg/80 px-2 py-1 text-[11px] text-muted">
          {simTimeLabel(simTime)} <span className="text-dim">t={tick}</span>
        </div>
      )}
      {resetOpen && (
        <div className="fade-in flex items-center gap-2 rounded border border-border bg-bg/90 p-1">
          <Select label="Scale" value={scale} onChange={(e) => setScale(e.target.value)} options={SCALES.map((s) => ({ value: s, label: s }))} />
          <Input label="Seed" value={seed} onChange={(e) => setSeed(e.target.value)} className="w-24" inputMode="numeric" />
          <Select label="Strategy" value={strategy} onChange={(e) => setStrategy(e.target.value)} options={strategyOptions} />
          <Button
            size="xs"
            variant="danger"
            loading={controlBusy}
            onClick={() => {
              void control({ action: "reset", scale, seed: Number(seed) || 42, strategy });
              setResetOpen(false);
            }}
          >
            Apply reset
          </Button>
        </div>
      )}
    </div>
  );
}
