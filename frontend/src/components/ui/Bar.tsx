import { C } from "@/lib/colors";

interface BarProps {
  /** 0..1 (values above 1 are clamped for width but the color can signal overflow) */
  value: number;
  color?: string;
  track?: string;
  height?: number;
  className?: string;
  /** optional marker line at this fraction (e.g. capacity) */
  marker?: number;
  title?: string;
}

export function Bar({ value, color = C.accent, track = "#1a222c", height = 6, className = "", marker, title }: BarProps) {
  const w = Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0)) * 100;
  return (
    <div className={`relative w-full overflow-hidden rounded-sm ${className}`} style={{ height, background: track }} title={title}>
      <div className="h-full rounded-sm transition-[width] duration-300" style={{ width: `${w}%`, background: color }} />
      {marker !== undefined && Number.isFinite(marker) && (
        <div
          className="absolute top-0 h-full w-px bg-text/60"
          style={{ left: `${Math.max(0, Math.min(100, marker * 100))}%` }}
        />
      )}
    </div>
  );
}

export function BatteryBar({ value, className = "" }: { value: number; className?: string }) {
  const color = value < 20 ? C.bad : value < 40 ? C.warn : C.good;
  return (
    <div className={`flex items-center gap-2 ${className}`}>
      <Bar value={value / 100} color={color} className="w-14" />
      <span className="num w-9 text-right text-[11px]" style={{ color }}>
        {Math.round(value)}%
      </span>
    </div>
  );
}
