"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Pause, Play, RefreshCw, SkipBack, SkipForward } from "lucide-react";
import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { TOOLTIP_STYLE } from "@/components/decisions/TimelineCompareChart";
import { EventRow } from "@/components/twin/EventFeed";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Panel } from "@/components/ui/Panel";
import { Empty, ErrorBanner, Loading, Skeleton } from "@/components/ui/State";
import { errorMessage } from "@/lib/api";
import { api } from "@/lib/client";
import { C, eventColor } from "@/lib/colors";
import { pct, simTimeShort, ticksToClock } from "@/lib/format";
import type { TimelineResponse, WorldSnapshot } from "@/lib/types";
import { useTwinStore } from "@/store/twinStore";

const TwinView2D = dynamic(() => import("@/components/twin/TwinView2D"), { ssr: false, loading: () => <Loading label="Loading 2D view" /> });

export default function TimelinePage() {
  const [data, setData] = useState<TimelineResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedTick, setSelectedTick] = useState<number | null>(null);
  const [snapshot, setSnapshot] = useState<WorldSnapshot | null>(null);
  const [snapLoading, setSnapLoading] = useState(false);
  const [snapError, setSnapError] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const running = useTwinStore((s) => s.status?.running ?? false);
  const tickSeconds = useTwinStore((s) => s.tickSeconds);
  const cache = useRef(new Map<number, WorldSnapshot>());

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const t = await api.timeline();
      setData(t);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => void load(), 15_000);
    return () => clearInterval(t);
  }, [running, load]);

  const snapshots = useMemo(() => data?.snapshots.slice().sort((a, b) => a.tick - b.tick) ?? [], [data]);
  const idx = selectedTick === null ? -1 : snapshots.findIndex((s) => s.tick === selectedTick);

  const selectSnapshot = useCallback(async (tick: number) => {
    setSelectedTick(tick);
    const cached = cache.current.get(tick);
    if (cached) {
      setSnapshot(cached);
      return;
    }
    setSnapLoading(true);
    setSnapError(null);
    try {
      const w = await api.snapshot(tick);
      cache.current.set(tick, w);
      setSnapshot(w);
    } catch (e) {
      setSnapError(errorMessage(e));
    } finally {
      setSnapLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!playing || snapshots.length === 0) return;
    const t = setInterval(() => {
      const cur = snapshots.findIndex((s) => s.tick === selectedTick);
      const next = snapshots[(cur + 1) % snapshots.length];
      void selectSnapshot(next.tick);
    }, 1500);
    return () => clearInterval(t);
  }, [playing, snapshots, selectedTick, selectSnapshot]);

  const rows = useMemo(
    () =>
      data?.points.map((p) => ({
        tick: p.tick,
        breach: p.breach_projected,
        congestion: p.congestion,
        utilization: p.utilization ?? 0,
        open: p.open,
        delivered: p.delivered,
      })) ?? [],
    [data],
  );
  const events = useMemo(() => data?.notable_events.slice().sort((a, b) => b.tick - a.tick) ?? [], [data]);

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 overflow-y-auto p-4">
      <div className="flex items-center gap-2">
        <span className="label">KPI history</span>
        <span className="num text-[11px] text-dim">
          {rows.length} points · {snapshots.length} snapshots · {events.length} notable events
        </span>
        <span className="text-[10px] text-dim">· breach &amp; utilization on the left axis, open orders right, congestion index on its own scale (see tooltip)</span>
        <Button size="xs" className="ml-auto" icon={<RefreshCw size={11} />} loading={loading} onClick={() => void load()}>
          Refresh
        </Button>
      </div>
      {error && <ErrorBanner message={error} onRetry={() => void load()} />}
      <Panel label="Timeline" title="SLA breach · congestion · utilization (left) · open orders (right) · event markers">
        {loading && rows.length === 0 ? (
          <Skeleton lines={5} />
        ) : rows.length === 0 ? (
          <Empty title="No timeline points yet" hint="Points are recorded as the simulation advances." />
        ) : (
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={rows} margin={{ top: 12, right: 8, left: -12, bottom: 0 }}>
              <CartesianGrid stroke={C.border} strokeDasharray="2 4" />
              <XAxis dataKey="tick" type="number" domain={["dataMin", "dataMax"]} tickFormatter={(v: number) => ticksToClock(v, tickSeconds)} stroke={C.dim} tickLine={false} />
              <YAxis yAxisId="ratio" domain={[0, 1]} tickFormatter={(v: number) => `${Math.round(v * 100)}%`} stroke={C.dim} tickLine={false} />
              <YAxis yAxisId="count" orientation="right" stroke={C.dim} tickLine={false} width={40} />
              {/* congestion is an index (robots over capacity), not a ratio: own hidden scale */}
              <YAxis yAxisId="cong" hide domain={[0, (max: number) => Math.max(1, max * 1.2)]} />
              <Tooltip
                {...TOOLTIP_STYLE}
                labelFormatter={(v) => `t=${v} · ${ticksToClock(Number(v), tickSeconds)}`}
                formatter={(v: unknown, name: unknown) => [String(name) === "open orders" || String(name) === "delivered" ? String(v) : typeof v === "number" ? (String(name) === "congestion" ? v.toFixed(2) : pct(v)) : String(v), String(name)]}
              />
              <Legend iconType="plainline" wrapperStyle={{ fontSize: 11 }} />
              {events.slice(0, 40).map((ev) => (
                <ReferenceLine key={`${ev.id}-${ev.seq}`} yAxisId="ratio" x={ev.tick} stroke={eventColor(ev.type)} strokeDasharray="2 3" strokeOpacity={0.7} label={{ value: ev.type.replace(/_/g, " ").toLowerCase(), position: "insideTopRight", fill: eventColor(ev.type), fontSize: 9, angle: -90, dx: 0, dy: 40 }} />
              ))}
              {selectedTick !== null && <ReferenceLine yAxisId="ratio" x={selectedTick} stroke={C.text} strokeWidth={1.5} />}
              <Line yAxisId="ratio" type="monotone" dataKey="breach" name="SLA breach (proj.)" stroke={C.bad} strokeWidth={2} dot={false} isAnimationActive={false} />
              <Line yAxisId="cong" type="monotone" dataKey="congestion" name="congestion" stroke={C.warn} strokeWidth={1.5} dot={false} isAnimationActive={false} />
              <Line yAxisId="ratio" type="monotone" dataKey="utilization" name="utilization" stroke={C.accent} strokeWidth={1.5} dot={false} isAnimationActive={false} />
              <Line yAxisId="count" type="monotone" dataKey="open" name="open orders" stroke={C.violet} strokeWidth={1.5} strokeDasharray="3 3" dot={false} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        )}
      </Panel>

      <div className="grid grid-cols-[1fr_360px] gap-3">
        <Panel
          label="Snapshot playback"
          title={selectedTick !== null ? `t=${selectedTick} · ${ticksToClock(selectedTick, tickSeconds)}` : undefined}
          actions={
            <>
              <Button size="xs" variant="ghost" icon={<SkipBack size={11} />} disabled={snapshots.length === 0 || idx <= 0} onClick={() => void selectSnapshot(snapshots[Math.max(0, idx - 1)].tick)} aria-label="Previous snapshot" />
              <Button size="xs" variant={playing ? "outline" : "primary"} icon={playing ? <Pause size={11} /> : <Play size={11} />} disabled={snapshots.length === 0} onClick={() => setPlaying((p) => !p)}>
                {playing ? "Pause" : "Play"}
              </Button>
              <Button size="xs" variant="ghost" icon={<SkipForward size={11} />} disabled={snapshots.length === 0 || idx >= snapshots.length - 1} onClick={() => void selectSnapshot(snapshots[Math.min(snapshots.length - 1, idx + 1)].tick)} aria-label="Next snapshot" />
            </>
          }
          padded={false}
        >
          <div className="flex h-[420px] flex-col">
            <div className="relative min-h-0 flex-1">
              {snapError && <ErrorBanner message={snapError} className="absolute inset-x-3 top-3 z-10" />}
              {snapLoading && !snapshot && <Loading label="Loading snapshot" />}
              {snapshot?.grid ? (
                <TwinView2D world={snapshot} robots={snapshot.robots} zoneOccupancy={snapshot.zone_occupancy} />
              ) : (
                !snapLoading && <Empty title="Select a snapshot" hint="Snapshots are taken periodically; pick one below to replay the world as it was." />
              )}
              {snapshot && (
                <div className="pointer-events-none absolute left-3 top-3 flex items-center gap-2 rounded border border-border bg-bg/80 px-2 py-1 text-[10px] backdrop-blur">
                  <Badge color={C.accent}>snapshot</Badge>
                  <span className="num text-text">{simTimeShort(snapshot.clock.sim_time)}</span>
                  <span className="num text-dim">
                    {snapshot.robots.length} robots · {snapshot.summary.orders_open} open
                  </span>
                </div>
              )}
            </div>
            <div className="border-t border-border p-2">
              {snapshots.length === 0 ? (
                <div className="text-center text-[11px] text-dim">No snapshots yet.</div>
              ) : (
                <>
                  <input
                    type="range"
                    min={0}
                    max={snapshots.length - 1}
                    value={Math.max(0, idx)}
                    onChange={(e) => void selectSnapshot(snapshots[Number(e.target.value)].tick)}
                    className="w-full"
                    aria-label="Snapshot scrubber"
                  />
                  <div className="mt-1 flex gap-1 overflow-x-auto pb-1">
                    {snapshots.map((s) => (
                      <button
                        key={s.tick}
                        type="button"
                        onClick={() => void selectSnapshot(s.tick)}
                        className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] num transition-colors ${s.tick === selectedTick ? "border-accent text-accent" : "border-border text-muted hover:text-text"}`}
                        title={`${s.sim_time} · ${s.digest.slice(0, 8)} · ${Math.round(s.size_bytes / 1024)} KB`}
                      >
                        {ticksToClock(s.tick, tickSeconds)}
                        {typeof s.kpis.sla_breach_rate_projected === "number" && <span className="text-dim"> · {pct(s.kpis.sla_breach_rate_projected, 0)}</span>}
                      </button>
                    ))}
                  </div>
                </>
              )}
            </div>
          </div>
        </Panel>
        <Panel label="Notable events" padded={false}>
          {events.length === 0 ? (
            <Empty title="No notable events" />
          ) : (
            <ul className="max-h-[420px] overflow-y-auto">
              {events.map((ev) => (
                <EventRow key={`${ev.id}-${ev.seq}`} ev={ev} tickSeconds={tickSeconds} />
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </div>
  );
}
