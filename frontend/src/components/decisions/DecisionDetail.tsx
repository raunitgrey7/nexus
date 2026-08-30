"use client";

import { useEffect, useState } from "react";
import { Check, Play, ShieldAlert, Sparkles, X } from "lucide-react";
import { useShallow } from "zustand/react/shallow";
import { Badge, RiskBadge } from "@/components/ui/Badge";
import { Bar } from "@/components/ui/Bar";
import { Button } from "@/components/ui/Button";
import { Panel } from "@/components/ui/Panel";
import { KV } from "@/components/ui/Table";
import { C, DECISION_STATUS_COLORS, severityColor } from "@/lib/colors";
import { fixed, ms, pct, simTimeLabel } from "@/lib/format";
import type { DecisionModel, PlanModel } from "@/lib/types";
import { useDecisionStore } from "@/store/decisionStore";
import { CandidatesTable } from "./CandidatesTable";
import { TimelineCompareChart, type Series } from "./TimelineCompareChart";

function paramsToText(params: Record<string, unknown>): string {
  return Object.entries(params)
    .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
    .join("  ");
}

function scalar(v: unknown): string {
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(3);
  if (typeof v === "string" || typeof v === "boolean") return String(v);
  return "";
}

/** Flatten the free-form `situation` dict one level deep into readable key/value rows. */
function situationText(s: Record<string, unknown>): { summary: string | null; rest: [string, string][] } {
  const summary = typeof s.summary === "string" ? s.summary : typeof s.description === "string" ? s.description : null;
  const rest: [string, string][] = [];
  for (const [k, v] of Object.entries(s)) {
    if (k === "summary" || k === "description" || v === null || v === undefined) continue;
    let text = "";
    if (Array.isArray(v)) {
      if (v.length === 0) text = "none";
      else if (v.every((x) => typeof x !== "object")) text = v.map(scalar).join(", ");
      else {
        const ids = v.map((x) => (x && typeof x === "object" && "id" in x ? String((x as { id: unknown }).id) : null)).filter(Boolean);
        text = ids.length ? `${v.length}: ${ids.join(", ")}` : `${v.length} items`;
      }
    } else if (typeof v === "object") {
      text = Object.entries(v as Record<string, unknown>)
        .filter(([, x]) => typeof x !== "object" || x === null)
        .map(([kk, x]) => `${kk.replace(/_/g, " ")} ${scalar(x)}`)
        .join(" · ");
    } else text = scalar(v);
    if (!text) continue;
    rest.push([k, text.length > 180 ? `${text.slice(0, 177)}…` : text]);
  }
  return { summary, rest: rest.slice(0, 14) };
}

export function DecisionDetail({ decision, compact = false }: { decision: DecisionModel; compact?: boolean }) {
  const { act, acting, error } = useDecisionStore(useShallow((s) => ({ act: s.act, acting: s.acting, error: s.error })));
  const [selectedPlanId, setSelectedPlanId] = useState<string | null>(null);
  useEffect(() => setSelectedPlanId(null), [decision.id]);

  const recommended = decision.candidates.find((c) => c.id === decision.recommended_plan_id) ?? decision.candidates[0] ?? null;
  const plan: PlanModel | null =
    selectedPlanId === "__baseline" ? null : selectedPlanId ? (decision.candidates.find((c) => c.id === selectedPlanId) ?? recommended) : recommended;
  const showingBaseline = selectedPlanId === "__baseline";

  const series: Series[] = [];
  if (decision.baseline) series.push({ id: "baseline", name: "Baseline", color: C.muted, points: decision.baseline.timeline, dashed: true });
  if (plan?.simulation) series.push({ id: "plan", name: plan.id === decision.recommended_plan_id ? "Recommended" : plan.name.slice(0, 24), color: plan.id === decision.recommended_plan_id ? C.accent : C.violet, points: plan.simulation.timeline });

  const baseBreach = decision.baseline?.kpis.sla_breach_rate_projected;
  const recBreach = recommended?.simulation?.kpis.sla_breach_rate_projected;
  const { summary, rest } = situationText(decision.situation);
  const status = decision.status;
  const canApprove = status === "proposed";
  const canReject = status === "proposed" || status === "approved";
  const canExecute = status === "approved";
  const actOn = (action: "approve" | "reject" | "execute") => void act(decision.id, { action, plan_id: plan?.id ?? decision.recommended_plan_id });

  return (
    <div className={`flex flex-col gap-3 ${compact ? "" : ""}`}>
      {/* header */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="num text-sm font-semibold text-text">{decision.id}</span>
        <Badge color={DECISION_STATUS_COLORS[status]} variant={status === "executed" ? "solid" : "soft"}>
          {status}
        </Badge>
        <Badge color={C.warn}>trigger · {decision.trigger}</Badge>
        {decision.llm_used ? (
          <Badge color={C.violet} title={decision.llm_model ?? undefined}>
            <Sparkles size={10} /> LLM {decision.llm_model ? `· ${decision.llm_model}` : ""}
          </Badge>
        ) : (
          <Badge color={C.muted}>deterministic planner</Badge>
        )}
        <span className="num ml-auto text-[10px] text-dim">
          {simTimeLabel(decision.sim_time)} · t={decision.created_tick}
        </span>
      </div>
      <div className="text-[11px] text-muted">
        Goal: <span className="text-text">{decision.goal}</span>
      </div>

      {/* headline */}
      <div className="grid grid-cols-3 gap-2">
        <div className="panel p-2">
          <div className="label">Candidates evaluated</div>
          <div className="num text-xl text-text">{decision.candidates_evaluated}</div>
          <div className="text-[10px] text-dim">{decision.candidates.length} shown</div>
        </div>
        <div className="panel p-2">
          <div className="label">SLA breach · baseline → plan</div>
          <div className="num text-xl">
            <span className="text-bad">{pct(baseBreach)}</span>
            <span className="mx-1 text-dim">→</span>
            <span className="text-good">{pct(recBreach)}</span>
          </div>
          <div className="text-[10px] text-dim">
            {baseBreach !== undefined && recBreach !== undefined ? `${((baseBreach - recBreach) * 100).toFixed(1)} pp gain` : "—"}
          </div>
        </div>
        <div className="panel p-2">
          <div className="label">Risk · approval</div>
          <div className="mt-1 flex items-center gap-1.5">
            <RiskBadge level={recommended?.risk?.level} />
            <Badge color={decision.approval.auto_approved ? C.good : C.muted}>{decision.approval.auto_approved ? "auto-approved" : decision.approval.policy}</Badge>
          </div>
          <div className="mt-1 truncate text-[10px] text-dim" title={decision.approval.reason}>
            {decision.approval.reason}
          </div>
        </div>
      </div>

      {/* situation */}
      <Panel label="Situation">
        {summary && <p className="text-xs leading-relaxed text-text">{summary}</p>}
        {rest.length > 0 && (
          <div className={`mt-2 grid gap-x-4 ${compact ? "grid-cols-1" : "grid-cols-2"}`}>
            {rest.map(([k, v]) => (
              <KV key={k} k={k.replace(/_/g, " ")} v={v} />
            ))}
          </div>
        )}
        {!summary && rest.length === 0 && <span className="text-xs text-dim">No situation data.</span>}
      </Panel>

      {decision.explanation && (
        <Panel label="Explanation">
          <p className="text-xs leading-relaxed text-muted">{decision.explanation}</p>
        </Panel>
      )}

      {/* candidates */}
      <Panel label="Baseline vs candidates" padded={false}>
        {decision.candidates.length === 0 ? (
          <div className="p-3 text-xs text-dim">No candidate plans.</div>
        ) : (
          <CandidatesTable decision={decision} selectedPlanId={selectedPlanId} onSelect={setSelectedPlanId} compact={compact} />
        )}
      </Panel>

      {/* plan detail */}
      {plan && !showingBaseline && (
        <Panel label={plan.id === decision.recommended_plan_id ? "Recommended plan" : "Selected plan"} title={plan.id}>
          <div className="text-xs font-semibold text-text">{plan.name}</div>
          {plan.description && <p className="mt-1 text-[11px] text-muted">{plan.description}</p>}
          <ul className="mt-2 flex flex-col gap-1.5">
            {plan.actions.map((a, i) => (
              <li key={i} className="rounded border border-border bg-bg/60 p-2">
                <div className="flex items-center gap-2">
                  <Badge color={a.type === "NOOP" ? C.dim : C.violet}>{a.type}</Badge>
                  <span className="num truncate text-[10px] text-muted" title={JSON.stringify(a.params)}>
                    {paramsToText(a.params)}
                  </span>
                </div>
                {a.rationale && <div className="mt-1 text-[11px] text-text/80">{a.rationale}</div>}
              </li>
            ))}
          </ul>
          {plan.risk && (
            <div className="mt-3">
              <div className="mb-1 flex items-center gap-2">
                <span className="label flex items-center gap-1">
                  <ShieldAlert size={11} /> Risk findings
                </span>
                <RiskBadge level={plan.risk.level} />
                <span className="num text-[10px] text-dim">
                  score {fixed(plan.risk.score, 2)} · {plan.risk.checked_seeds} seed{plan.risk.checked_seeds === 1 ? "" : "s"}
                </span>
              </div>
              {plan.risk.findings.length === 0 ? (
                <div className="text-[11px] text-dim">No findings.</div>
              ) : (
                <ul className="flex flex-col gap-1">
                  {plan.risk.findings.map((f, i) => (
                    <li key={i} className="flex gap-2 text-[11px]">
                      <Badge color={severityColor(f.severity)}>{f.severity}</Badge>
                      <span className="text-muted">
                        <span className="text-text/80">{f.kind}</span> — {f.message}
                        {f.entity_ids.length > 0 && <span className="num text-dim"> [{f.entity_ids.join(", ")}]</span>}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
              {Object.keys(plan.risk.stability).length > 0 && (
                <div className="mt-1 num text-[10px] text-dim">
                  stability: {Object.entries(plan.risk.stability).map(([k, v]) => `${k}=${typeof v === "number" ? v.toFixed(3) : String(v)}`).join(" · ")}
                </div>
              )}
            </div>
          )}
          {plan.simulation && Object.keys(plan.simulation.delta_vs_baseline).length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {Object.entries(plan.simulation.delta_vs_baseline).map(([k, v]) => (
                <Badge key={k} color={v <= 0 === (k.includes("throughput") || k.includes("utilization")) ? C.warn : C.good} mono>
                  Δ {k.replace(/_/g, " ")} {v > 0 ? "+" : ""}
                  {k.includes("rate") ? `${(v * 100).toFixed(1)} pp` : v.toFixed(2)}
                </Badge>
              ))}
            </div>
          )}
        </Panel>
      )}

      {/* chart */}
      <Panel label="Simulated horizon" title={plan?.simulation ? `${Math.round(plan.simulation.horizon_ticks / 60)} min · baseline vs ${showingBaseline ? "baseline" : "plan"}` : undefined}>
        <TimelineCompareChart series={series} height={compact ? 190 : 240} />
      </Panel>

      {/* approval */}
      <Panel label="Approval">
        <div className="flex flex-wrap items-center gap-2">
          <KV k="policy" v={decision.approval.policy} />
          <span className="text-dim">·</span>
          <KV k="by" v={decision.approval.approved_by ?? "—"} />
          <span className="text-dim">·</span>
          <KV k="at tick" v={decision.approval.approved_tick ?? "—"} />
          <div className="ml-auto flex items-center gap-1.5">
            <Button size="xs" variant="good" icon={<Check size={12} />} disabled={!canApprove || acting} loading={acting && canApprove} onClick={() => actOn("approve")}>
              Approve
            </Button>
            <Button size="xs" variant="danger" icon={<X size={12} />} disabled={!canReject || acting} onClick={() => actOn("reject")}>
              Reject
            </Button>
            <Button size="xs" variant="primary" icon={<Play size={12} />} disabled={!canExecute || acting} loading={acting && canExecute} onClick={() => actOn("execute")}>
              Execute
            </Button>
          </div>
        </div>
        {error && <div className="mt-2 text-[11px] text-bad">{error}</div>}
        {!canApprove && !canExecute && !canReject && <div className="mt-1 text-[10px] text-dim">This decision is {status}; no further actions.</div>}
        {status === "proposed" && <div className="mt-1 text-[10px] text-dim">Execute requires approval first.</div>}
      </Panel>

      {/* timings */}
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="label">Timings</span>
        {Object.entries(decision.timings).map(([k, v]) => (
          <span key={k} className="flex items-center gap-1 rounded border border-border px-1.5 py-0.5 text-[10px]">
            <span className="text-dim">{k.replace(/_ms$/, "").replace(/_/g, " ")}</span>
            <span className="num text-text">{ms(v)}</span>
          </span>
        ))}
        {Object.keys(decision.timings).length === 0 && <span className="text-[10px] text-dim">—</span>}
      </div>
      {plan?.simulation && (
        <div className="flex items-center gap-2">
          <span className="label">Plan score</span>
          <Bar value={Math.min(1, plan.simulation.score / 2)} color={C.violet} className="w-32" />
          <span className="num text-[10px] text-muted">{fixed(plan.simulation.score, 3)} (lower is better)</span>
        </div>
      )}
    </div>
  );
}
