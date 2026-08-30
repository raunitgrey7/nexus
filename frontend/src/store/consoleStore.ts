import { create } from "zustand";
import { api } from "@/lib/client";
import { errorMessage } from "@/lib/api";
import type { NLQResponse } from "@/lib/types";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  response?: NLQResponse;
  error?: string;
  at: number;
}

interface ConsoleState {
  messages: ChatMessage[];
  pending: boolean;
  horizonMin: number;
  ask(question: string): Promise<void>;
  setHorizon(h: number): void;
  clear(): void;
}

let n = 0;

export const useConsoleStore = create<ConsoleState>((set, get) => ({
  messages: [],
  pending: false,
  horizonMin: 60,

  async ask(question) {
    const q = question.trim();
    if (!q || get().pending) return;
    n++;
    const user: ChatMessage = { id: `m${n}`, role: "user", text: q, at: Date.now() };
    set((s) => ({ messages: [...s.messages, user], pending: true }));
    try {
      const response = await api.nlq({ question: q, horizon_min: get().horizonMin });
      n++;
      set((s) => ({ pending: false, messages: [...s.messages, { id: `m${n}`, role: "assistant", text: response.answer, response, at: Date.now() }] }));
    } catch (e) {
      n++;
      set((s) => ({ pending: false, messages: [...s.messages, { id: `m${n}`, role: "assistant", text: "", error: errorMessage(e), at: Date.now() }] }));
    }
  },

  setHorizon(h) {
    set({ horizonMin: h });
  },

  clear() {
    set({ messages: [] });
  },
}));
