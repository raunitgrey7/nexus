import type { HTMLAttributes, ReactNode, TdHTMLAttributes, ThHTMLAttributes } from "react";

export function Table({ children, className = "", ...rest }: HTMLAttributes<HTMLTableElement>) {
  return (
    <div className="w-full overflow-auto">
      <table {...rest} className={`w-full border-collapse text-xs ${className}`}>
        {children}
      </table>
    </div>
  );
}

export function Th({ children, className = "", align, ...rest }: ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      {...rest}
      className={`sticky top-0 z-[1] bg-panel px-2 py-1.5 text-left label font-semibold ${align === "right" ? "text-right" : ""} ${className}`}
    >
      {children}
    </th>
  );
}

export function Td({ children, className = "", align, ...rest }: TdHTMLAttributes<HTMLTableCellElement>) {
  return (
    <td
      {...rest}
      className={`border-t border-border/70 px-2 py-1.5 align-middle ${align === "right" ? "text-right num" : ""} ${className}`}
    >
      {children}
    </td>
  );
}

export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
  className = "",
}: {
  tabs: { id: T; label: ReactNode; count?: number }[];
  value: T;
  onChange: (v: T) => void;
  className?: string;
}) {
  return (
    <div className={`flex items-center gap-0.5 border-b border-border ${className}`}>
      {tabs.map((t) => (
        <button
          key={t.id}
          type="button"
          onClick={() => onChange(t.id)}
          className={`-mb-px flex items-center gap-1.5 border-b-2 px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wider transition-colors ${
            value === t.id ? "border-accent text-accent" : "border-transparent text-muted hover:text-text"
          }`}
        >
          {t.label}
          {t.count !== undefined && <span className="num rounded bg-panel-2 px-1 text-[10px] text-muted">{t.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function KV({ k, v, mono = true }: { k: string; v: ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-border/60 py-1 last:border-0">
      <span className="text-[11px] text-muted">{k}</span>
      <span className={`text-right text-xs text-text ${mono ? "num" : ""}`}>{v}</span>
    </div>
  );
}
