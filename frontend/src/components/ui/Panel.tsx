import type { ReactNode } from "react";

interface PanelProps {
  label?: string;
  title?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  padded?: boolean;
}

/** Bordered surface with the small uppercase label every panel carries. */
export function Panel({ label, title, actions, children, className = "", bodyClassName = "", padded = true }: PanelProps) {
  return (
    <section className={`panel flex min-h-0 flex-col ${className}`}>
      {(label || title || actions) && (
        <header className="flex items-center justify-between gap-2 border-b border-border px-3 py-1.5">
          <div className="flex min-w-0 items-baseline gap-2">
            {label && <span className="label">{label}</span>}
            {title && <span className="truncate text-xs text-text">{title}</span>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-1">{actions}</div>}
        </header>
      )}
      <div className={`min-h-0 flex-1 ${padded ? "p-3" : ""} ${bodyClassName}`}>{children}</div>
    </section>
  );
}
