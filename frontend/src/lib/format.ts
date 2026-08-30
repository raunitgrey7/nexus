/** Number / time formatting helpers shared by every page. */

export function pct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

export function num(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

export function fixed(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toFixed(digits);
}

export function minutes(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${v.toFixed(digits)} min`;
}

export function ms(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  if (v >= 1000) return `${(v / 1000).toFixed(2)} s`;
  return `${Math.round(v)} ms`;
}

/** Ticks → `HH:MM:SS` of simulated duration. */
export function ticksToClock(ticks: number, tickSeconds = 1): string {
  const total = Math.max(0, Math.floor(ticks * tickSeconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

/** Ticks → compact `12m 04s` / `-3m 10s` (negative = overdue). */
export function ticksToCountdown(ticks: number, tickSeconds = 1): string {
  const sign = ticks < 0 ? "-" : "";
  const total = Math.abs(Math.floor(ticks * tickSeconds));
  const m = Math.floor(total / 60);
  const s = total % 60;
  if (m >= 60) return `${sign}${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
  return `${sign}${m}m ${String(s).padStart(2, "0")}s`;
}

export function ticksToMinutes(ticks: number, tickSeconds = 1): number {
  return (ticks * tickSeconds) / 60;
}

/** ISO sim_time → `Mon 08:42:10`. */
export function simTimeLabel(iso: string | null | undefined): string {
  if (!iso) return "--:--:--";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const day = d.toLocaleDateString("en-US", { weekday: "short", timeZone: "UTC" });
  const time = d.toLocaleTimeString("en-GB", { hour12: false, timeZone: "UTC" });
  return `${day} ${time}`;
}

export function simTimeShort(iso: string | null | undefined): string {
  if (!iso) return "--:--";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString("en-GB", { hour12: false, timeZone: "UTC", hour: "2-digit", minute: "2-digit" });
}

export function titleCase(s: string): string {
  return s.replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function shortId(id: string, n = 10): string {
  return id.length > n ? `${id.slice(0, n)}…` : id;
}

export function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}

/** Signed delta with arrow. `invert` = lower is better. */
export function delta(v: number | null | undefined, opts: { pct?: boolean; digits?: number } = {}): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const sign = v > 0 ? "+" : "";
  const body = opts.pct ? `${(v * 100).toFixed(opts.digits ?? 1)} pp` : v.toFixed(opts.digits ?? 1);
  return `${sign}${body}`;
}
