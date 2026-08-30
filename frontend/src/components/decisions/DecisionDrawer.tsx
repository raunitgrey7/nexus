"use client";

import Link from "next/link";
import { ExternalLink, X } from "lucide-react";
import { useShallow } from "zustand/react/shallow";
import { Button } from "@/components/ui/Button";
import { Empty, ErrorBanner, Loading } from "@/components/ui/State";
import { selectedDecision, useDecisionStore } from "@/store/decisionStore";
import { DecisionDetail } from "./DecisionDetail";

export function DecisionDrawer() {
  const { open, close, decision, creating, error, clearError } = useDecisionStore(
    useShallow((s) => ({ open: s.drawerOpen, close: s.closeDrawer, decision: selectedDecision(s), creating: s.creating, error: s.error, clearError: s.clearError })),
  );
  if (!open) return null;
  return (
    <div className="absolute inset-y-0 right-0 z-30 flex w-[560px] max-w-[92vw] flex-col border-l border-border bg-panel shadow-2xl slide-in-right">
      <header className="flex items-center gap-2 border-b border-border px-3 py-2">
        <span className="label">Decision</span>
        <span className="text-xs text-text">{creating ? "Running agent pipeline…" : (decision?.id ?? "—")}</span>
        <div className="ml-auto flex items-center gap-1">
          {decision && (
            <Link href="/decisions" className="inline-flex items-center gap-1 text-[11px] text-accent hover:underline">
              Open page <ExternalLink size={11} />
            </Link>
          )}
          <Button size="xs" variant="ghost" icon={<X size={13} />} onClick={close} aria-label="Close" />
        </div>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {creating && !decision && <Loading label="Planning → validating → optimizing → simulating → risk" />}
        {error && <ErrorBanner message={error} onRetry={clearError} className="mb-3" />}
        {creating && decision && (
          <div className="mb-3 flex items-center gap-2 rounded border border-violet/40 bg-violet/10 px-3 py-1.5 text-[11px] text-violet">
            <span className="h-2 w-2 animate-ping rounded-full bg-violet" /> Running agent pipeline — showing the previous decision meanwhile.
          </div>
        )}
        {!creating && !decision && !error && <Empty title="No decision yet" hint="Click “Decide now” to run the plan → simulate → risk → approve pipeline." />}
        {decision && <DecisionDetail decision={decision} compact />}
      </div>
    </div>
  );
}
