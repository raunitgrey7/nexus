"use client";

import type { ReactNode } from "react";
import { AlertTriangle, Inbox, RefreshCw } from "lucide-react";
import { Button } from "./Button";

export function Empty({ icon, title, hint, action }: { icon?: ReactNode; title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="flex h-full min-h-[120px] flex-col items-center justify-center gap-2 p-6 text-center">
      <div className="text-dim">{icon ?? <Inbox size={22} />}</div>
      <div className="text-xs text-muted">{title}</div>
      {hint && <div className="max-w-sm text-[11px] text-dim">{hint}</div>}
      {action}
    </div>
  );
}

export function Skeleton({ lines = 4, className = "" }: { lines?: number; className?: string }) {
  return (
    <div className={`flex flex-col gap-2 p-3 ${className}`} aria-busy="true">
      {Array.from({ length: lines }).map((_, i) => (
        <div
          key={i}
          className="h-3 animate-pulse rounded bg-panel-2"
          style={{ width: `${70 + ((i * 37) % 30)}%`, animationDelay: `${i * 80}ms` }}
        />
      ))}
    </div>
  );
}

export function ErrorBanner({ message, onRetry, className = "" }: { message: string; onRetry?: () => void; className?: string }) {
  return (
    <div className={`flex items-center gap-2 rounded border border-bad/40 bg-bad/10 px-3 py-2 text-xs text-bad ${className}`}>
      <AlertTriangle size={14} className="shrink-0" />
      <span className="min-w-0 flex-1 truncate" title={message}>
        {message}
      </span>
      {onRetry && (
        <Button size="xs" variant="danger" icon={<RefreshCw size={11} />} onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex h-full min-h-[120px] items-center justify-center gap-2 text-xs text-muted">
      <span className="h-2 w-2 animate-ping rounded-full bg-accent" />
      {label}…
    </div>
  );
}
