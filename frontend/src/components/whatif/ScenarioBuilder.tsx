"use client";

import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Input, Select, Toggle } from "@/components/ui/Select";
import { strategyColor } from "@/lib/colors";
import type { MutationModel, MutationType, WhatIfRequest } from "@/lib/types";
import { MUTATION_PARAMS, MUTATION_TYPES, defaultMutation } from "./mutations";

interface Props {
  value: WhatIfRequest;
  onChange: (v: WhatIfRequest) => void;
  strategies: string[];
}

export function ScenarioBuilder({ value, onChange, strategies }: Props) {
  const setScenario = (patch: Partial<WhatIfRequest["scenario"]>) => onChange({ ...value, scenario: { ...value.scenario, ...patch } });
  const setMutation = (i: number, m: MutationModel) => {
    const mutations = value.scenario.mutations.slice();
    mutations[i] = m;
    setScenario({ mutations });
  };
  const toggleStrategy = (s: string) => {
    const has = value.strategies.includes(s);
    const next = has ? value.strategies.filter((x) => x !== s) : [...value.strategies, s];
    onChange({ ...value, strategies: next.length ? next : value.strategies });
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-[1fr_2fr] gap-2">
        <Input label="Name" value={value.scenario.name} onChange={(e) => setScenario({ name: e.target.value })} />
        <Input label="Description" value={value.scenario.description} onChange={(e) => setScenario({ description: e.target.value })} />
      </div>

      <div>
        <div className="mb-1 flex items-center justify-between">
          <span className="label">Mutations</span>
          <Select
            value=""
            onChange={(e) => {
              if (!e.target.value) return;
              setScenario({ mutations: [...value.scenario.mutations, defaultMutation(e.target.value as MutationType)] });
            }}
            options={[{ value: "", label: "+ add mutation" }, ...MUTATION_TYPES.map((t) => ({ value: t, label: t }))]}
          />
        </div>
        {value.scenario.mutations.length === 0 && <div className="rounded border border-dashed border-border p-3 text-center text-[11px] text-dim">No mutations — the scenario equals the current world.</div>}
        <ul className="flex flex-col gap-1.5">
          {value.scenario.mutations.map((m, i) => (
            <li key={i} className="rounded border border-border bg-bg/60 p-2">
              <div className="flex flex-wrap items-center gap-2">
                <Select
                  value={m.type}
                  onChange={(e) => setMutation(i, defaultMutation(e.target.value as MutationType))}
                  options={MUTATION_TYPES.map((t) => ({ value: t, label: t }))}
                />
                {MUTATION_PARAMS[m.type].map((p) => {
                  const raw = m.params[p.key];
                  return (
                    <Input
                      key={p.key}
                      label={p.label}
                      className="w-auto"
                      type={p.kind === "number" ? "number" : "text"}
                      step={p.step}
                      value={p.kind === "list" ? (Array.isArray(raw) ? (raw as unknown[]).join(",") : String(raw ?? "")) : String(raw ?? "")}
                      onChange={(e) => {
                        const v = e.target.value;
                        const parsed: unknown = p.kind === "number" ? Number(v) : p.kind === "list" ? v.split(",").map((x) => x.trim()).filter(Boolean) : v;
                        setMutation(i, { ...m, params: { ...m.params, [p.key]: parsed } });
                      }}
                      style={{ width: p.kind === "list" ? 110 : 70 }}
                    />
                  );
                })}
                <Input label="at (min)" type="number" value={String(m.at_min)} onChange={(e) => setMutation(i, { ...m, at_min: Math.max(0, Number(e.target.value) || 0) })} style={{ width: 60 }} className="w-auto" />
                <Button size="xs" variant="ghost" icon={<Trash2 size={11} />} onClick={() => setScenario({ mutations: value.scenario.mutations.filter((_, j) => j !== i) })} aria-label="Remove mutation" />
              </div>
            </li>
          ))}
        </ul>
        <Button size="xs" variant="ghost" icon={<Plus size={11} />} className="mt-1" onClick={() => setScenario({ mutations: [...value.scenario.mutations, defaultMutation("DEMAND_MULTIPLIER")] })}>
          Add mutation
        </Button>
      </div>

      <div className="grid grid-cols-[1fr_auto_auto] items-end gap-3">
        <div>
          <div className="label mb-1">Strategies</div>
          <div className="flex flex-wrap gap-1">
            {strategies.map((s) => {
              const on = value.strategies.includes(s);
              return (
                <button
                  key={s}
                  type="button"
                  onClick={() => toggleStrategy(s)}
                  className="rounded border px-2 py-0.5 text-[11px] transition-colors"
                  style={{ borderColor: on ? strategyColor(s) : "#1f2933", color: on ? strategyColor(s) : "#5b6874", background: on ? `${strategyColor(s)}1a` : "transparent" }}
                >
                  {s}
                </button>
              );
            })}
          </div>
        </div>
        <label className="flex flex-col gap-1">
          <span className="label">Horizon · {value.horizon_min} min</span>
          <input type="range" min={5} max={480} step={5} value={value.horizon_min} onChange={(e) => onChange({ ...value, horizon_min: Number(e.target.value) })} className="w-36" />
        </label>
        <div className="flex items-center gap-3">
          <Select label="Seeds" value={String(value.seeds)} onChange={(e) => onChange({ ...value, seeds: Number(e.target.value) })} options={[1, 2, 3, 4, 5].map((n) => ({ value: String(n), label: String(n) }))} />
          <Toggle checked={value.include_current} onChange={(v) => onChange({ ...value, include_current: v })} label="Reference run" />
        </div>
      </div>
    </div>
  );
}
