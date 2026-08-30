"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { BarChart3, Box, FlaskConical, GitBranch, History, Terminal, TrendingUp } from "lucide-react";

export const NAV = [
  { href: "/", label: "Live Twin", icon: Box },
  { href: "/decisions", label: "Decisions", icon: GitBranch },
  { href: "/whatif", label: "What-If Lab", icon: FlaskConical },
  { href: "/forecast", label: "Forecast", icon: TrendingUp },
  { href: "/console", label: "Console", icon: Terminal },
  { href: "/timeline", label: "Timeline", icon: History },
  { href: "/benchmarks", label: "Benchmarks", icon: BarChart3 },
] as const;

export function pageTitle(pathname: string): string {
  const hit = NAV.find((n) => (n.href === "/" ? pathname === "/" : pathname.startsWith(n.href)));
  return hit?.label ?? "NEXUS";
}

export function Rail() {
  const pathname = usePathname();
  return (
    <nav className="flex flex-col items-center gap-1 border-r border-border bg-panel py-2" aria-label="Primary">
      {NAV.map(({ href, label, icon: Icon }) => {
        const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            title={label}
            aria-label={label}
            aria-current={active ? "page" : undefined}
            className={`group relative flex h-10 w-10 items-center justify-center rounded-md transition-colors ${
              active ? "bg-accent/15 text-accent" : "text-muted hover:bg-panel-2 hover:text-text"
            }`}
          >
            {active && <span className="absolute left-[-6px] h-5 w-[2px] rounded bg-accent" />}
            <Icon size={18} strokeWidth={1.75} />
            <span className="pointer-events-none absolute left-12 z-20 hidden whitespace-nowrap rounded border border-border bg-panel px-2 py-1 text-[11px] text-text shadow-lg group-hover:block">
              {label}
            </span>
          </Link>
        );
      })}
    </nav>
  );
}
