"use client";

import { useMemo, useState } from "react";
import { Empty } from "@/components/ui/State";
import { Toggle } from "@/components/ui/Select";
import { eventColor, NOTABLE_EVENT_TYPES } from "@/lib/colors";
import { ticksToClock } from "@/lib/format";
import type { EventModel } from "@/lib/types";
import { useTwinStore } from "@/store/twinStore";

export function summarisePayload(ev: EventModel): string {
  const p = ev.payload ?? {};
  const parts: string[] = [];
  for (const [k, v] of Object.entries(p)) {
    if (v === null || v === undefined) continue;
    if (typeof v === "object") {
      if (Array.isArray(v)) parts.push(`${k}=${v.length > 4 ? `${v.length} items` : JSON.stringify(v)}`);
      else continue;
    } else if (typeof v === "number") parts.push(`${k}=${Number.isInteger(v) ? v : v.toFixed(2)}`);
    else parts.push(`${k}=${String(v)}`);
    if (parts.length >= 4) break;
  }
  return parts.join(" · ");
}

export function EventRow({ ev, tickSeconds }: { ev: EventModel; tickSeconds: number }) {
  const color = eventColor(ev.type);
  const notable = NOTABLE_EVENT_TYPES.has(ev.type);
  return (
    <li className={`flex gap-2 border-b border-border/60 px-3 py-1.5 text-[11px] ${notable ? "bg-panel-2/40" : ""}`}>
      <span className="mt-[5px] h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: color, boxShadow: notable ? `0 0 6px ${color}` : undefined }} />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="font-semibold" style={{ color }}>
            {ev.type}
          </span>
          {ev.entity_id && <span className="num text-text">{ev.entity_id}</span>}
          <span className="num ml-auto shrink-0 text-[10px] text-dim">{ticksToClock(ev.tick, tickSeconds)}</span>
        </div>
        <div className="num truncate text-[10px] text-muted" title={JSON.stringify(ev.payload)}>
          {summarisePayload(ev) || <span className="text-dim">{ev.origin}</span>}
        </div>
      </div>
    </li>
  );
}

export function EventFeed() {
  const events = useTwinStore((s) => s.events);
  const tickSeconds = useTwinStore((s) => s.tickSeconds);
  const [notableOnly, setNotableOnly] = useState(false);
  const list = useMemo(() => {
    const src = notableOnly ? events.filter((e) => NOTABLE_EVENT_TYPES.has(e.type)) : events;
    return src.slice(-200).reverse();
  }, [events, notableOnly]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between border-b border-border px-3 py-1.5">
        <span className="num text-[10px] text-dim">{events.length} buffered</span>
        <Toggle checked={notableOnly} onChange={setNotableOnly} label="Notable only" />
      </div>
      {list.length === 0 ? (
        <Empty title="No events yet" hint="Start the simulation or inject a fault to see the live feed." />
      ) : (
        <ul className="min-h-0 flex-1 overflow-y-auto">
          {list.map((ev) => (
            <EventRow key={`${ev.id}-${ev.seq}`} ev={ev} tickSeconds={tickSeconds} />
          ))}
        </ul>
      )}
    </div>
  );
}
