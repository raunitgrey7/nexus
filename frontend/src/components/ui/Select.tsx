"use client";

import type { SelectHTMLAttributes } from "react";

interface Option {
  value: string;
  label: string;
}

interface SelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, "children"> {
  options: Option[];
  label?: string;
}

export function Select({ options, label, className = "", ...rest }: SelectProps) {
  return (
    <label className={`inline-flex items-center gap-1.5 ${className}`}>
      {label && <span className="label">{label}</span>}
      <select
        {...rest}
        className="h-7 rounded border border-border bg-panel-2 px-1.5 text-xs text-text outline-none focus:border-accent"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string;
}

export function Input({ label, className = "", ...rest }: InputProps) {
  return (
    <label className={`inline-flex items-center gap-1.5 ${className}`}>
      {label && <span className="label">{label}</span>}
      <input
        {...rest}
        className="h-7 w-full rounded border border-border bg-panel-2 px-2 text-xs text-text outline-none placeholder:text-dim focus:border-accent num"
      />
    </label>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label?: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className="inline-flex items-center gap-1.5 disabled:opacity-50"
    >
      <span
        className={`relative inline-block h-4 w-7 rounded-full border transition-colors ${checked ? "border-accent bg-accent/30" : "border-border bg-panel-2"}`}
      >
        <span
          className={`absolute top-[2px] h-[10px] w-[10px] rounded-full transition-all ${checked ? "left-[15px] bg-accent" : "left-[2px] bg-muted"}`}
        />
      </span>
      {label && <span className="label">{label}</span>}
    </button>
  );
}
