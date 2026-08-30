"use client";

import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Loader2 } from "lucide-react";

type Variant = "primary" | "ghost" | "outline" | "danger" | "violet" | "good";
type Size = "xs" | "sm" | "md";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  icon?: ReactNode;
  active?: boolean;
}

const VARIANTS: Record<Variant, string> = {
  primary: "bg-accent/15 text-accent border-accent/50 hover:bg-accent/25 glow-accent",
  ghost: "bg-transparent text-muted border-transparent hover:bg-panel-2 hover:text-text",
  outline: "bg-transparent text-text border-border hover:border-muted hover:bg-panel-2",
  danger: "bg-bad/10 text-bad border-bad/40 hover:bg-bad/20",
  violet: "bg-violet/10 text-violet border-violet/40 hover:bg-violet/20",
  good: "bg-good/10 text-good border-good/40 hover:bg-good/20",
};

const SIZES: Record<Size, string> = {
  xs: "h-6 px-2 text-[11px] gap-1",
  sm: "h-7 px-2.5 text-xs gap-1.5",
  md: "h-8 px-3 text-xs gap-2",
};

export function Button({
  variant = "outline",
  size = "sm",
  loading,
  icon,
  active,
  className = "",
  children,
  disabled,
  ...rest
}: ButtonProps) {
  return (
    <button
      {...rest}
      disabled={disabled || loading}
      className={`inline-flex items-center justify-center rounded border font-medium whitespace-nowrap transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${VARIANTS[variant]} ${SIZES[size]} ${active ? "ring-1 ring-accent/60 text-accent" : ""} ${className}`}
    >
      {loading ? <Loader2 size={13} className="animate-spin" /> : icon}
      {children}
    </button>
  );
}

export function IconButton({ className = "", ...rest }: ButtonProps) {
  return <Button {...rest} className={`px-0 w-7 ${className}`} />;
}
