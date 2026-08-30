"use client";

import { useEffect, useRef } from "react";
import { C, congestionTint, robotColor } from "@/lib/colors";
import type { RobotModel, WorldSnapshot } from "@/lib/types";
import { useTwinStore } from "@/store/twinStore";

interface Props {
  world: WorldSnapshot;
  /** read robots / occupancy from the live store and interpolate; otherwise draw `robots` once */
  live?: boolean;
  robots?: RobotModel[];
  zoneOccupancy?: Record<string, number>;
  selectedRobotId?: string | null;
  onSelectRobot?: (id: string | null) => void;
  showLabels?: boolean;
  className?: string;
}

interface Layout {
  s: number;
  ox: number;
  oy: number;
  W: number;
  H: number;
}

function layoutFor(cw: number, ch: number, W: number, H: number): Layout {
  const pad = 16;
  const s = Math.max(1, Math.min((cw - pad * 2) / W, (ch - pad * 2) / H));
  return { s, ox: (cw - W * s) / 2, oy: (ch - H * s) / 2, W, H };
}

function px(l: Layout, x: number): number {
  return l.ox + x * l.s;
}
function py(l: Layout, y: number): number {
  return l.oy + (l.H - 1 - y) * l.s;
}

function drawStatic(ctx: CanvasRenderingContext2D, world: WorldSnapshot, l: Layout, occupancy: Record<string, number>, dockOpen: Record<string, boolean>) {
  const grid = world.grid!;
  const { s } = l;
  ctx.fillStyle = "#0c1016";
  ctx.fillRect(l.ox, l.oy, l.W * s, l.H * s);
  // grid lines
  ctx.lineWidth = 1;
  for (let x = 0; x <= l.W; x++) {
    ctx.strokeStyle = x % 10 === 0 ? "#243040" : "#141b24";
    ctx.beginPath();
    ctx.moveTo(px(l, x), l.oy);
    ctx.lineTo(px(l, x), l.oy + l.H * s);
    ctx.stroke();
  }
  for (let y = 0; y <= l.H; y++) {
    ctx.strokeStyle = y % 10 === 0 ? "#243040" : "#141b24";
    ctx.beginPath();
    ctx.moveTo(l.ox, l.oy + y * s);
    ctx.lineTo(l.ox + l.W * s, l.oy + y * s);
    ctx.stroke();
  }
  // zones
  const closed = new Set(grid.closed_zones ?? []);
  for (const z of world.zones) {
    const w = z.x1 - z.x0 + 1;
    const h = z.y1 - z.y0 + 1;
    const x = px(l, z.x0);
    const y = py(l, z.y1);
    const congested = z.kind === "storage" || z.kind === "corridor";
    const ratio = z.capacity > 0 ? (occupancy[z.id] ?? 0) / z.capacity : 0;
    const isClosed = closed.has(z.id) || z.closed;
    const tint = isClosed ? C.bad : congested ? congestionTint(ratio) : z.kind === "dock" ? C.warn : C.good;
    ctx.globalAlpha = isClosed ? 0.22 : congested ? 0.05 + Math.min(0.3, ratio * 0.2) : 0.04;
    ctx.fillStyle = tint;
    ctx.fillRect(x, y, w * s, h * s);
    ctx.globalAlpha = 1;
    if (isClosed) {
      ctx.save();
      ctx.beginPath();
      ctx.rect(x, y, w * s, h * s);
      ctx.clip();
      ctx.strokeStyle = "rgba(239,68,68,0.5)";
      ctx.lineWidth = 2;
      for (let i = -h * s; i < w * s + h * s; i += 10) {
        ctx.beginPath();
        ctx.moveTo(x + i, y);
        ctx.lineTo(x + i + h * s, y + h * s);
        ctx.stroke();
      }
      ctx.restore();
    }
    ctx.strokeStyle = `${tint}55`;
    ctx.lineWidth = 1;
    ctx.strokeRect(x + 0.5, y + 0.5, w * s - 1, h * s - 1);
    // label
    const big = z.kind === "storage";
    ctx.fillStyle = big ? "rgba(230,237,243,0.35)" : "rgba(139,152,165,0.6)";
    ctx.font = `${big ? 700 : 600} ${big ? Math.max(12, s * 3) : Math.max(8, s * 1.1)}px ui-monospace, Menlo, monospace`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    const cx = x + (w * s) / 2;
    const cy = y + (h * s) / 2;
    if (z.kind === "corridor" && h > w) {
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(-Math.PI / 2);
      ctx.fillText(z.id, 0, 0);
      ctx.restore();
    } else {
      ctx.fillText(big ? z.id : z.kind === "corridor" ? z.id : z.name.toUpperCase(), cx, cy);
    }
  }
  // cells
  for (let y = 0; y < grid.height; y++) {
    const row = grid.rows[y] ?? "";
    for (let x = 0; x < grid.width; x++) {
      const t = row[x];
      if (t === "0") continue;
      const X = px(l, x);
      const Y = py(l, y);
      if (t === "1") {
        ctx.fillStyle = "#334155";
        ctx.fillRect(X + s * 0.1, Y + s * 0.1, s * 0.8, s * 0.8);
      } else if (t === "2") {
        ctx.fillStyle = "#1f2933";
        ctx.fillRect(X, Y, s, s);
      } else if (t === "6") {
        ctx.fillStyle = "#141b24";
        ctx.fillRect(X + 1, Y + 1, s - 2, s - 2);
      } else if (t === "5") {
        ctx.fillStyle = "#2a3644";
        ctx.fillRect(X + 1, Y + s * 0.3, s - 2, s * 0.4);
      }
    }
  }
  for (const d of world.docks) {
    const open = dockOpen[d.id] ?? d.open;
    ctx.fillStyle = open ? C.warn : "#5a2a2a";
    ctx.fillRect(px(l, d.cell[0]) + 1, py(l, d.cell[1]) + 1, s - 2, s - 2);
    ctx.fillStyle = "#0a0d12";
    ctx.font = `700 ${Math.max(7, s * 0.55)}px ui-monospace, monospace`;
    ctx.fillText(d.id, px(l, d.cell[0]) + s / 2, py(l, d.cell[1]) + s / 2);
  }
  for (const c of world.chargers) {
    ctx.fillStyle = c.enabled ? C.good : "#2a4a2a";
    ctx.fillRect(px(l, c.cell[0]) + 1, py(l, c.cell[1]) + 1, s - 2, s - 2);
    ctx.fillStyle = "#0a0d12";
    ctx.font = `700 ${Math.max(6, s * 0.4)}px ui-monospace, monospace`;
    ctx.fillText(c.id.replace("CH", ""), px(l, c.cell[0]) + s / 2, py(l, c.cell[1]) + s / 2);
  }
  for (const [x, y] of grid.blocked ?? []) {
    ctx.fillStyle = "rgba(239,68,68,0.85)";
    ctx.fillRect(px(l, x) + 1, py(l, y) + 1, s - 2, s - 2);
  }
}

function drawRobot(ctx: CanvasRenderingContext2D, l: Layout, r: RobotModel, x: number, y: number, selected: boolean, showLabel: boolean, t: number) {
  const s = l.s;
  const cx = px(l, x) + s / 2;
  const cy = py(l, y) + s / 2;
  const color = robotColor(r.status);
  // path
  if (r.path.length) {
    ctx.strokeStyle = `${color}aa`;
    ctx.lineWidth = Math.max(1, s * 0.12);
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    for (const [pxx, pyy] of r.path) ctx.lineTo(px(l, pxx) + s / 2, py(l, pyy) + s / 2);
    ctx.stroke();
  }
  const pulse = r.status === "failed" ? 0.5 + 0.5 * Math.abs(Math.sin(t * 5)) : 1;
  ctx.shadowColor = color;
  ctx.shadowBlur = s * 0.9 * pulse;
  ctx.fillStyle = color;
  ctx.globalAlpha = r.status === "failed" ? 0.4 + 0.6 * pulse : 1;
  ctx.beginPath();
  ctx.arc(cx, cy, Math.max(2.5, s * 0.36), 0, Math.PI * 2);
  ctx.fill();
  ctx.globalAlpha = 1;
  ctx.shadowBlur = 0;
  if (selected) {
    ctx.strokeStyle = "#e6edf3";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.arc(cx, cy, Math.max(5, s * 0.62), 0, Math.PI * 2);
    ctx.stroke();
  }
  if (showLabel && s >= 5) {
    ctx.fillStyle = "rgba(10,13,18,0.8)";
    const fw = s * 1.45;
    ctx.fillRect(cx - fw / 2, cy - s * 1.35, fw, s * 0.7);
    ctx.fillStyle = "#e6edf3";
    ctx.font = `600 ${Math.max(7, s * 0.5)}px ui-monospace, Menlo, monospace`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(r.id, cx, cy - s);
  }
}

export default function TwinView2D({ world, live, robots, zoneOccupancy, selectedRobotId, onSelectRobot, showLabels = true, className = "" }: Props) {
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const staticCanvas = useRef<HTMLCanvasElement | null>(null);
  const layout = useRef<Layout | null>(null);
  const positions = useRef(new Map<string, { x: number; y: number }>());
  const staticKey = useRef("");
  const propsRef = useRef({ robots, zoneOccupancy, selectedRobotId, showLabels, live });
  propsRef.current = { robots, zoneOccupancy, selectedRobotId, showLabels, live };

  useEffect(() => {
    const el = wrap.current;
    const cv = canvas.current;
    if (!el || !cv || !world.grid) return;
    const grid = world.grid;
    let raf = 0;
    let last = performance.now();
    const dpr = Math.min(2, window.devicePixelRatio || 1);

    const resize = () => {
      const { width, height } = el.getBoundingClientRect();
      cv.width = Math.max(1, Math.floor(width * dpr));
      cv.height = Math.max(1, Math.floor(height * dpr));
      cv.style.width = `${width}px`;
      cv.style.height = `${height}px`;
      layout.current = layoutFor(width, height, grid.width, grid.height);
      staticKey.current = "";
    };
    const ro = new ResizeObserver(resize);
    ro.observe(el);
    resize();

    const render = (now: number) => {
      raf = requestAnimationFrame(render);
      const l = layout.current;
      const ctx = cv.getContext("2d");
      if (!l || !ctx) return;
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      const p = propsRef.current;
      const store = p.live ? useTwinStore.getState() : null;
      const occupancy = store ? store.zoneOccupancy : (p.zoneOccupancy ?? world.zone_occupancy);
      const dockOpen = store ? store.dockOpen : {};
      const key = `${cv.width}x${cv.height}|${Object.entries(occupancy)
        .map(([k, v]) => `${k}:${v}`)
        .join(",")}|${Object.values(dockOpen).join(",")}|${(grid.blocked ?? []).length}|${(grid.closed_zones ?? []).join(",")}`;
      if (key !== staticKey.current) {
        if (!staticCanvas.current) staticCanvas.current = document.createElement("canvas");
        const sc = staticCanvas.current;
        sc.width = cv.width;
        sc.height = cv.height;
        const sctx = sc.getContext("2d");
        if (sctx) {
          sctx.setTransform(dpr, 0, 0, dpr, 0, 0);
          sctx.fillStyle = "#0a0d12";
          sctx.fillRect(0, 0, cv.width, cv.height);
          drawStatic(sctx, world, l, occupancy, dockOpen);
        }
        staticKey.current = key;
      }
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      if (staticCanvas.current) ctx.drawImage(staticCanvas.current, 0, 0);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const list: RobotModel[] = store ? store.robotIds.map((id) => store.robots[id]).filter(Boolean) : (p.robots ?? world.robots);
      const selected = store ? store.selectedRobotId : (p.selectedRobotId ?? null);
      const t = now / 1000;
      for (const r of list) {
        let pos = positions.current.get(r.id);
        if (!pos || !p.live) {
          pos = { x: r.cell[0], y: r.cell[1] };
          positions.current.set(r.id, pos);
        } else {
          const k = 1 - Math.exp(-dt * 9);
          if (Math.abs(pos.x - r.cell[0]) + Math.abs(pos.y - r.cell[1]) > 6) {
            pos.x = r.cell[0];
            pos.y = r.cell[1];
          } else {
            pos.x += (r.cell[0] - pos.x) * k;
            pos.y += (r.cell[1] - pos.y) * k;
          }
        }
        drawRobot(ctx, l, r, pos.x, pos.y, selected === r.id, p.showLabels, t);
      }
    };
    raf = requestAnimationFrame(render);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [world]);

  const onClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const l = layout.current;
    if (!l) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const store = live ? useTwinStore.getState() : null;
    const list: RobotModel[] = store ? store.robotIds.map((id) => store.robots[id]) : (robots ?? world.robots);
    let best: string | null = null;
    let bestD = Infinity;
    for (const r of list) {
      const pos = positions.current.get(r.id) ?? { x: r.cell[0], y: r.cell[1] };
      const cx = px(l, pos.x) + l.s / 2;
      const cy = py(l, pos.y) + l.s / 2;
      const d = Math.hypot(cx - mx, cy - my);
      if (d < bestD && d < Math.max(8, l.s)) {
        bestD = d;
        best = r.id;
      }
    }
    const next = best === (store ? store.selectedRobotId : selectedRobotId) ? null : best;
    if (store) store.selectRobot(next);
    onSelectRobot?.(next);
  };

  return (
    <div ref={wrap} className={`h-full w-full ${className}`}>
      <canvas ref={canvas} onClick={onClick} className="block cursor-crosshair" />
    </div>
  );
}
