"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ArrowRight, Bot, Sparkles, Terminal, Trash2, User } from "lucide-react";
import { useShallow } from "zustand/react/shallow";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Select } from "@/components/ui/Select";
import { Empty } from "@/components/ui/State";
import { C, strategyColor } from "@/lib/colors";
import { ms, pct } from "@/lib/format";
import type { NLQIntent, WhatIfResult } from "@/lib/types";
import { useConsoleStore, type ChatMessage } from "@/store/consoleStore";
import { useWhatIfStore } from "@/store/whatifStore";

const DEFAULT_SUGGESTIONS = ["Why are orders slowing down?", "What happens if order volume increases 40%?", "Which robot should charge next?"];

const INTENT_COLORS: Record<NLQIntent, string> = {
  explain: C.accent,
  whatif: C.violet,
  status: C.muted,
  forecast: C.warn,
  recommend: C.good,
  entity: C.blue,
  unknown: C.dim,
};

function WhatIfCard({ result }: { result: WhatIfResult }) {
  const upsert = useWhatIfStore((s) => s.upsert);
  const select = useWhatIfStore((s) => s.select);
  const byStrategy = new Map<string, number[]>();
  for (const r of result.runs) byStrategy.set(r.strategy, [...(byStrategy.get(r.strategy) ?? []), r.kpis.sla_breach_rate_projected]);
  return (
    <div className="mt-2 rounded border border-violet/40 bg-violet/5 p-2">
      <div className="flex items-center gap-2">
        <Badge color={C.violet}>what-if</Badge>
        <span className="text-[11px] text-text">{result.scenario.name}</span>
        {result.best_strategy && (
          <Badge color={strategyColor(result.best_strategy)} variant="solid" className="ml-auto">
            best: {result.best_strategy}
          </Badge>
        )}
      </div>
      <div className="mt-1.5 grid grid-cols-[auto_1fr_auto] gap-x-3 gap-y-0.5 text-[11px]">
        {result.reference && (
          <>
            <span className="text-dim">reference</span>
            <span />
            <span className="num text-dim">{pct(result.reference.kpis.sla_breach_rate_projected)}</span>
          </>
        )}
        {[...byStrategy.entries()].map(([s, vals]) => {
          const v = vals.reduce((a, b) => a + b, 0) / vals.length;
          return (
            <span key={s} className="contents">
              <span style={{ color: strategyColor(s) }}>{s}</span>
              <span className="self-center">
                <span className="block h-1.5 rounded" style={{ width: `${Math.min(100, v * 400)}%`, background: strategyColor(s) }} />
              </span>
              <span className="num text-text">{pct(v)}</span>
            </span>
          );
        })}
      </div>
      <Link
        href="/whatif"
        onClick={() => {
          upsert(result);
          select(result.id);
        }}
        className="mt-1.5 inline-flex items-center gap-1 text-[11px] text-violet hover:underline"
      >
        Open in What-If Lab <ArrowRight size={11} />
      </Link>
    </div>
  );
}

function Message({ m }: { m: ChatMessage }) {
  const isUser = m.role === "user";
  const r = m.response;
  return (
    <div className={`fade-in flex gap-3 ${isUser ? "flex-row-reverse" : ""}`}>
      <div className={`flex h-7 w-7 shrink-0 items-center justify-center rounded border ${isUser ? "border-border text-muted" : "border-violet/50 text-violet"}`}>
        {isUser ? <User size={13} /> : <Bot size={13} />}
      </div>
      <div className={`max-w-[78%] rounded border px-3 py-2 ${isUser ? "border-border bg-panel-2" : "border-border bg-panel"}`}>
        {m.error ? (
          <div className="text-xs text-bad">{m.error}</div>
        ) : (
          <div className="text-xs leading-relaxed text-text">
            {m.text.split("\n").map((line, i) => (
              <p key={i} className={i > 0 ? "mt-1.5" : ""}>
                {line}
              </p>
            ))}
          </div>
        )}
        {r && (
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <Badge color={INTENT_COLORS[r.intent] ?? C.dim}>{r.intent}</Badge>
            {r.llm_used ? (
              <Badge color={C.violet} title={r.model ?? undefined}>
                <Sparkles size={10} /> LLM{r.model ? ` · ${r.model}` : ""}
              </Badge>
            ) : (
              <Badge color={C.muted}>deterministic</Badge>
            )}
            <span className="num text-[10px] text-dim">{ms(r.latency_ms)}</span>
          </div>
        )}
        {r?.data?.whatif && <WhatIfCard result={r.data.whatif} />}
      </div>
    </div>
  );
}

export default function ConsolePage() {
  const { messages, ask, pending, horizonMin, setHorizon, clear } = useConsoleStore(
    useShallow((s) => ({ messages: s.messages, ask: s.ask, pending: s.pending, horizonMin: s.horizonMin, setHorizon: s.setHorizon, clear: s.clear })),
  );
  const [input, setInput] = useState("");
  const bottom = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, pending]);

  const last = [...messages].reverse().find((m) => m.role === "assistant" && m.response)?.response;
  const suggestions = last?.suggestions?.length ? last.suggestions : DEFAULT_SUGGESTIONS;

  const submit = (q: string) => {
    void ask(q);
    setInput("");
    inputRef.current?.focus();
  };

  return (
    <div className="mx-auto flex h-full min-h-0 max-w-4xl flex-col p-4">
      <div className="flex items-center gap-2 pb-3">
        <Terminal size={14} className="text-accent" />
        <span className="label">Natural-language console</span>
        <span className="text-[11px] text-dim">POST /api/nlq</span>
        <div className="ml-auto flex items-center gap-2">
          <Select label="Horizon" value={String(horizonMin)} onChange={(e) => setHorizon(Number(e.target.value))} options={[30, 60, 120, 240].map((h) => ({ value: String(h), label: `${h} min` }))} />
          <Button size="xs" variant="ghost" icon={<Trash2 size={11} />} onClick={clear} disabled={messages.length === 0}>
            Clear
          </Button>
        </div>
      </div>
      <div className="panel min-h-0 flex-1 overflow-y-auto p-4">
        {messages.length === 0 ? (
          <Empty
            icon={<Bot size={22} />}
            title="Ask the twin anything"
            hint="Explain slowdowns, run what-if scenarios, get charging recommendations or inspect entities. Answers are grounded in the live world state."
          />
        ) : (
          <div className="flex flex-col gap-4">
            {messages.map((m) => (
              <Message key={m.id} m={m} />
            ))}
            {pending && (
              <div className="flex items-center gap-2 text-[11px] text-muted">
                <span className="h-2 w-2 animate-ping rounded-full bg-violet" /> thinking…
              </div>
            )}
            <div ref={bottom} />
          </div>
        )}
      </div>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {suggestions.map((s) => (
          <button key={s} type="button" onClick={() => submit(s)} disabled={pending} className="rounded-full border border-border bg-panel px-2.5 py-1 text-[11px] text-muted transition-colors hover:border-accent/60 hover:text-accent disabled:opacity-50">
            {s}
          </button>
        ))}
      </div>
      <form
        className="mt-2 flex items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (input.trim()) submit(input);
        }}
      >
        <input
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="e.g. Why are orders slowing down?"
          className="h-9 flex-1 rounded border border-border bg-panel px-3 text-xs text-text outline-none placeholder:text-dim focus:border-accent"
          autoFocus
        />
        <Button type="submit" variant="primary" size="md" loading={pending} icon={<ArrowRight size={13} />} disabled={!input.trim()}>
          Ask
        </Button>
      </form>
    </div>
  );
}
