import { create } from "zustand";
import { api } from "@/lib/client";
import { errorMessage } from "@/lib/api";
import type { Forecast } from "@/lib/types";

interface ForecastState {
  forecast: Forecast | null;
  horizonMin: number;
  loading: boolean;
  error: string | null;
  updatedAt: number | null;

  load(horizonMin?: number): Promise<void>;
  setForecast(f: Forecast): void;
  setHorizon(h: number): void;
}

export const useForecastStore = create<ForecastState>((set, get) => ({
  forecast: null,
  horizonMin: 60,
  loading: false,
  error: null,
  updatedAt: null,

  async load(horizonMin) {
    const h = horizonMin ?? get().horizonMin;
    set({ loading: true, error: null, horizonMin: h });
    try {
      const forecast = await api.forecast(h);
      set({ forecast, loading: false, updatedAt: Date.now() });
    } catch (e) {
      set({ loading: false, error: errorMessage(e) });
    }
  },

  setForecast(forecast) {
    set({ forecast, updatedAt: Date.now() });
  },

  setHorizon(h) {
    set({ horizonMin: h });
  },
}));
