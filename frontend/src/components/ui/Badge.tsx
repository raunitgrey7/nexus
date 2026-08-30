import type { ReactNode } from "react";
import { C, riskColor } from "@/lib/colors";
import type { RiskLevel } from "@/lib/types";

interface BadgeProps {
  children: ReactNode;
  color?: string;
  variant?: "solid" | "outline" | "soft";
  className?: string;
  title?: string;
  pulse?: boolean;
  mono?: boolean;
}

export function Badge({ children, color = C.muted, variant = "soft", className = "", title, pulse, mono }: BadgeProps) {
  const style =
    variant === "solid"
      ? { background: color, color: "#0a0d12", borderColor: color }
      : variant === "outline"
        ? { color, borderColor: color }
        : { color, borderColor: `${color}55`, background: `${color}1a` };
  return (
    <span
      title={title}
      style={style}
      className={`inline-flex items-center gap-1 rounded border px-1.5 py-[1px] text-[10px] font-semibold uppercase tracking-wider whitespace-nowrap ${mono ? "num" : ""} ${pulse ? "pulse-bad" : ""} ${className}`}
    >
      {children}
    </span>
  );
}

export function RiskBadge({ level, className = "" }: { level: RiskLevel | string | null | undefined; className?: string }) {
  if (!level) return <Badge className={className}>—</Badge>;
  return (
    <Badge color={riskColor(level)} variant={level === "CRITICAL" ? "solid" : "soft"} className={className} pulse={level === "CRITICAL"}>
      {level}
    </Badge>
  );
}

export function Dot({ color, pulse, size = 8 }: { color: string; pulse?: boolean; size?: number }) {
  return (
    <span
      className={`inline-block rounded-full ${pulse ? "pulse-bad" : ""}`}
      style={{ width: size, height: size, background: color, boxShadow: `0 0 8px ${color}88` }}
    />
  );
}
