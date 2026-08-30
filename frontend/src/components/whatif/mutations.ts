import type { MutationModel, MutationType } from "@/lib/types";

export type ParamKind = "number" | "text" | "list";

export interface ParamSpec {
  key: string;
  label: string;
  kind: ParamKind;
  default: number | string | string[];
  step?: number;
}

export const MUTATION_TYPES: MutationType[] = [
  "ROBOT_FAILURE",
  "REMOVE_ROBOTS",
  "ADD_ROBOTS",
  "DEMAND_MULTIPLIER",
  "DEMAND_BURST",
  "CLOSE_ZONE",
  "CLOSE_DOCK",
  "DISABLE_CHARGERS",
  "BLOCK_AISLE",
  "MOVE_INVENTORY",
  "WORKER_DELAY",
  "SET_SLA",
  "SET_BATCHING",
];

export const MUTATION_PARAMS: Record<MutationType, ParamSpec[]> = {
  ROBOT_FAILURE: [
    { key: "robot_ids", label: "robots", kind: "list", default: ["R07"] },
    { key: "cause", label: "cause", kind: "text", default: "motor_fault" },
    { key: "recovery_min", label: "recovery (min)", kind: "number", default: 45 },
  ],
  REMOVE_ROBOTS: [{ key: "count", label: "count", kind: "number", default: 2 }],
  ADD_ROBOTS: [{ key: "count", label: "count", kind: "number", default: 2 }],
  DEMAND_MULTIPLIER: [{ key: "multiplier", label: "multiplier", kind: "number", default: 1.4, step: 0.1 }],
  DEMAND_BURST: [
    { key: "multiplier", label: "multiplier", kind: "number", default: 2, step: 0.1 },
    { key: "duration_min", label: "duration (min)", kind: "number", default: 30 },
  ],
  CLOSE_ZONE: [{ key: "zone_id", label: "zone", kind: "text", default: "B" }],
  CLOSE_DOCK: [{ key: "dock_id", label: "dock", kind: "text", default: "D2" }],
  DISABLE_CHARGERS: [{ key: "count", label: "count", kind: "number", default: 2 }],
  BLOCK_AISLE: [
    { key: "zone_id", label: "zone", kind: "text", default: "C" },
    { key: "aisles", label: "aisles", kind: "number", default: 1 },
  ],
  MOVE_INVENTORY: [
    { key: "from_zone", label: "from", kind: "text", default: "C" },
    { key: "to_zone", label: "to", kind: "text", default: "B" },
    { key: "skus", label: "skus", kind: "number", default: 6 },
    { key: "units", label: "units", kind: "number", default: 40 },
  ],
  WORKER_DELAY: [
    { key: "worker_ids", label: "workers", kind: "list", default: ["W01"] },
    { key: "minutes", label: "minutes", kind: "number", default: 30 },
  ],
  SET_SLA: [
    { key: "NORMAL", label: "NORMAL (min)", kind: "number", default: 8 },
    { key: "HIGH", label: "HIGH (min)", kind: "number", default: 4 },
  ],
  SET_BATCHING: [{ key: "orders_per_trip", label: "orders / trip", kind: "number", default: 3 }],
};

export function defaultMutation(type: MutationType): MutationModel {
  const params: Record<string, unknown> = {};
  for (const p of MUTATION_PARAMS[type]) params[p.key] = Array.isArray(p.default) ? [...p.default] : p.default;
  return { type, params, at_min: 0 };
}

export function describeMutation(m: MutationModel): string {
  const parts = Object.entries(m.params).map(([k, v]) => `${k}=${Array.isArray(v) ? v.join(",") : String(v)}`);
  return `${m.type}${parts.length ? ` (${parts.join(", ")})` : ""}${m.at_min ? ` @${m.at_min}m` : ""}`;
}
