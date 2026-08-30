import { create } from "zustand";
import { api } from "@/lib/client";
import { errorMessage } from "@/lib/api";
import type { WhatIfPreset, WhatIfRequest, WhatIfResult } from "@/lib/types";

interface WhatIfState {
  presets: WhatIfPreset[];
  history: WhatIfResult[];
  currentId: string | null;
  loading: boolean;
  loaded: boolean;
  running: boolean;
  error: string | null;

  load(): Promise<void>;
  run(req: WhatIfRequest): Promise<WhatIfResult | null>;
  select(id: string | null): void;
  upsert(r: WhatIfResult): void;
  clearError(): void;
}

function merge(list: WhatIfResult[], r: WhatIfResult): WhatIfResult[] {
  const i = list.findIndex((x) => x.id === r.id);
  if (i === -1) return [r, ...list];
  const next = list.slice();
  next[i] = r;
  return next;
}

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

export const useWhatIfStore = create<WhatIfState>((set, get) => ({
  presets: [],
  history: [],
  currentId: null,
  loading: false,
  loaded: false,
  running: false,
  error: null,

  async load() {
    set({ loading: true, error: null });
    const [presets, history] = await Promise.allSettled([api.whatifPresets(), api.whatifs()]);
    const errors: string[] = [];
    if (presets.status === "fulfilled") set({ presets: presets.value });
    else errors.push(errorMessage(presets.reason));
    if (history.status === "fulfilled") {
      set((s) => ({ history: history.value, currentId: s.currentId ?? history.value[0]?.id ?? null }));
    } else errors.push(errorMessage(history.reason));
    set({ loading: false, loaded: true, error: errors.length ? errors.join(" · ") : null });
  },

  async run(req) {
    set({ running: true, error: null });
    try {
      let result = await api.createWhatIf(req);
      set((s) => ({ history: merge(s.history, result), currentId: result.id }));
      const started = Date.now();
      while (result.status === "queued" || result.status === "running") {
        if (Date.now() - started > 240_000) throw new Error("What-if evaluation timed out");
        await sleep(1000);
        const live = get().history.find((r) => r.id === result.id);
        if (live && (live.status === "done" || live.status === "failed")) {
          result = live; // completed via the websocket frame
          break;
        }
        result = await api.whatif(result.id);
        set((s) => ({ history: merge(s.history, result) }));
      }
      set({ running: false });
      return result;
    } catch (e) {
      set({ running: false, error: errorMessage(e) });
      return null;
    }
  },

  select(id) {
    set({ currentId: id });
  },

  upsert(r) {
    set((s) => ({ history: merge(s.history, r), currentId: s.currentId ?? r.id }));
  },

  clearError() {
    set({ error: null });
  },
}));

export function currentWhatIf(s: WhatIfState): WhatIfResult | null {
  return s.history.find((r) => r.id === s.currentId) ?? null;
}
