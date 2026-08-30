import type { DecisionStatus, LowMedHigh, RiskLevel, RobotStatus, Severity, WhatIfStatus } from "./types";

export const C = {
  bg: "#0a0d12",
  panel: "#11161d",
  panel2: "#161c25",
  border: "#1f2933",
  text: "#e6edf3",
  muted: "#8b98a5",
  dim: "#5b6874",
  accent: "#22d3ee",
  good: "#22c55e",
  warn: "#f59e0b",
  bad: "#ef4444",
  violet: "#a78bfa",
  blue: "#60a5fa",
  slate: "#334155",
  slateLight: "#475569",
  grid: "#1a222c",
} as const;

export const ROBOT_COLORS: Record<RobotStatus, string> = {
  idle: "#8b98a5",
  moving: C.accent,
  picking: C.violet,
  unloading: C.violet,
  delivering: C.blue,
  to_charger: "#86efac",
  charging: C.good,
  waiting: C.warn,
  failed: C.bad,
  maintenance: "#f97316",
};

export function robotColor(status: RobotStatus | string): string {
  return ROBOT_COLORS[status as RobotStatus] ?? C.muted;
}

export const RISK_COLORS: Record<RiskLevel, string> = {
  LOW: C.good,
  MEDIUM: C.warn,
  HIGH: "#f97316",
  CRITICAL: C.bad,
};

export function riskColor(level: RiskLevel | string | null | undefined): string {
  return RISK_COLORS[level as RiskLevel] ?? C.muted;
}

export const LMH_COLORS: Record<LowMedHigh, string> = { low: C.good, medium: C.warn, high: C.bad };

export function lmhColor(level: LowMedHigh | string): string {
  return LMH_COLORS[level as LowMedHigh] ?? C.muted;
}

export const SEVERITY_COLORS: Record<Severity, string> = {
  info: C.muted,
  low: C.good,
  medium: C.warn,
  high: "#f97316",
  critical: C.bad,
};

export function severityColor(s: Severity | string): string {
  return SEVERITY_COLORS[s as Severity] ?? C.muted;
}

export const DECISION_STATUS_COLORS: Record<DecisionStatus, string> = {
  proposed: C.violet,
  approved: C.accent,
  executed: C.good,
  rejected: C.muted,
  failed: C.bad,
};

export const WHATIF_STATUS_COLORS: Record<WhatIfStatus, string> = {
  queued: C.muted,
  running: C.accent,
  done: C.good,
  failed: C.bad,
};

export const STRATEGY_COLORS: Record<string, string> = {
  baseline: "#8b98a5",
  optimized: C.blue,
  ai_planner: C.violet,
  nexus_full: C.accent,
  reference: "#5b6874",
  current: "#5b6874",
};

export function strategyColor(name: string): string {
  return STRATEGY_COLORS[name] ?? "#f472b6";
}

export const PRIORITY_COLORS: Record<string, string> = {
  LOW: C.dim,
  NORMAL: C.muted,
  HIGH: C.warn,
  CRITICAL: C.bad,
};

/** Occupancy / capacity → teal → amber → red. */
export function congestionColor(ratio: number): string {
  if (!Number.isFinite(ratio)) return C.accent;
  if (ratio < 0.6) return C.accent;
  if (ratio < 1) return C.warn;
  return C.bad;
}

/** Interpolated congestion tint used by the floor overlays (returns hex). */
export function congestionTint(ratio: number): string {
  const r = Math.max(0, Math.min(1.5, Number.isFinite(ratio) ? ratio : 0));
  const teal: [number, number, number] = [34, 211, 238];
  const amber: [number, number, number] = [245, 158, 11];
  const red: [number, number, number] = [239, 68, 68];
  let a: [number, number, number];
  let b: [number, number, number];
  let t: number;
  if (r <= 0.7) {
    a = teal;
    b = amber;
    t = r / 0.7;
  } else {
    a = amber;
    b = red;
    t = Math.min(1, (r - 0.7) / 0.8);
  }
  const mix = a.map((c, i) => Math.round(c + (b[i] - c) * t));
  return `#${mix.map((c) => c.toString(16).padStart(2, "0")).join("")}`;
}

/** Event type → feed color. */
export function eventColor(type: string): string {
  if (type.startsWith("PLAN_")) return C.violet;
  if (type === "ROBOT_FAILURE" || type.endsWith("_CLOSED") || type === "AISLE_BLOCKED" || type === "CHARGER_DISABLED")
    return C.bad;
  if (type === "BATTERY_LOW" || type === "WORKER_DELAY" || type === "ORDER_CANCELLED" || type === "DEMAND_CHANGED")
    return C.warn;
  if (
    type === "ROBOT_RECOVERED" ||
    type.endsWith("_OPENED") ||
    type === "AISLE_CLEARED" ||
    type === "ORDER_DELIVERED" ||
    type === "CHARGER_ENABLED"
  )
    return C.good;
  if (type.startsWith("TASK_") || type === "INVENTORY_MOVED") return C.accent;
  return C.muted;
}

export const NOTABLE_EVENT_TYPES = new Set([
  "ROBOT_FAILURE",
  "ROBOT_RECOVERED",
  "BATTERY_LOW",
  "AISLE_BLOCKED",
  "AISLE_CLEARED",
  "ZONE_CLOSED",
  "ZONE_OPENED",
  "DOCK_CLOSED",
  "DOCK_OPENED",
  "CHARGER_DISABLED",
  "CHARGER_ENABLED",
  "WORKER_DELAY",
  "DEMAND_CHANGED",
  "PLAN_PROPOSED",
  "PLAN_APPROVED",
  "PLAN_REJECTED",
  "PLAN_EXECUTED",
  "ORDER_CANCELLED",
  "TASK_REASSIGNED",
  "INVENTORY_MOVED",
]);

/** System-risk badge derived from the headline KPIs. */
export function systemRisk(breach: number | undefined, congestion: number | undefined, failed: number): RiskLevel {
  const b = breach ?? 0;
  const c = congestion ?? 0;
  if (b >= 0.25 || c >= 2 || failed >= 3) return "CRITICAL";
  if (b >= 0.12 || c >= 1 || failed >= 2) return "HIGH";
  if (b >= 0.06 || c >= 0.4 || failed >= 1) return "MEDIUM";
  return "LOW";
}
