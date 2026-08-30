import { create } from "zustand";
import { api } from "@/lib/client";
import { errorMessage } from "@/lib/api";
import type { DecisionActionRequest, DecisionModel, DecisionRequest } from "@/lib/types";

interface DecisionState {
  decisions: DecisionModel[];
  selectedId: string | null;
  loading: boolean;
  loaded: boolean;
  creating: boolean;
  acting: boolean;
  error: string | null;
  drawerOpen: boolean;

  load(limit?: number): Promise<void>;
  create(req?: DecisionRequest): Promise<DecisionModel | null>;
  act(id: string, req: DecisionActionRequest): Promise<DecisionModel | null>;
  select(id: string | null): void;
  upsert(d: DecisionModel): void;
  openDrawer(id?: string): void;
  closeDrawer(): void;
  clearError(): void;
}

function mergeDecision(list: DecisionModel[], d: DecisionModel): DecisionModel[] {
  const i = list.findIndex((x) => x.id === d.id);
  if (i === -1) return [d, ...list];
  const next = list.slice();
  next[i] = d;
  return next;
}

export const useDecisionStore = create<DecisionState>((set, get) => ({
  decisions: [],
  selectedId: null,
  loading: false,
  loaded: false,
  creating: false,
  acting: false,
  error: null,
  drawerOpen: false,

  async load(limit = 50) {
    set({ loading: true, error: null });
    try {
      const decisions = await api.decisions(limit);
      const selectedId = get().selectedId ?? decisions[0]?.id ?? null;
      set({ decisions, loading: false, loaded: true, selectedId });
    } catch (e) {
      set({ loading: false, loaded: true, error: errorMessage(e) });
    }
  },

  async create(req = {}) {
    set({ creating: true, error: null });
    try {
      const d = await api.createDecision({
        goal: "Minimize SLA breaches and fulfillment delay",
        trigger: "manual",
        horizon_min: 90,
        ...req,
      });
      set((s) => ({ creating: false, decisions: mergeDecision(s.decisions, d), selectedId: d.id }));
      return d;
    } catch (e) {
      set({ creating: false, error: errorMessage(e) });
      return null;
    }
  },

  async act(id, req) {
    set({ acting: true, error: null });
    try {
      const d = await api.decisionAction(id, { actor: "operator", note: "", ...req });
      set((s) => ({ acting: false, decisions: mergeDecision(s.decisions, d) }));
      return d;
    } catch (e) {
      set({ acting: false, error: errorMessage(e) });
      return null;
    }
  },

  select(id) {
    set({ selectedId: id });
  },

  upsert(d) {
    set((s) => ({ decisions: mergeDecision(s.decisions, d), selectedId: s.selectedId ?? d.id }));
  },

  openDrawer(id) {
    set((s) => ({ drawerOpen: true, selectedId: id ?? s.selectedId ?? s.decisions[0]?.id ?? null }));
  },

  closeDrawer() {
    set({ drawerOpen: false });
  },

  clearError() {
    set({ error: null });
  },
}));

export function selectedDecision(s: DecisionState): DecisionModel | null {
  return s.decisions.find((d) => d.id === s.selectedId) ?? null;
}

export function recommendedPlan(d: DecisionModel | null) {
  if (!d) return null;
  return d.candidates.find((c) => c.id === d.recommended_plan_id) ?? d.candidates.find((c) => c.rank === 1) ?? null;
}
