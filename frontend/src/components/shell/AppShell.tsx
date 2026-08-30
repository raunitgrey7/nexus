"use client";

import { useEffect, type ReactNode } from "react";
import { useTwinStore } from "@/store/twinStore";
import { Rail } from "./Rail";
import { TopBar } from "./TopBar";

export function AppShell({ children }: { children: ReactNode }) {
  const boot = useTwinStore((s) => s.boot);
  useEffect(() => {
    boot();
  }, [boot]);

  return (
    <div className="grid h-screen w-screen grid-cols-[52px_1fr] grid-rows-[44px_1fr] bg-bg text-text">
      <TopBar />
      <Rail />
      <main className="relative min-h-0 min-w-0 overflow-hidden">{children}</main>
    </div>
  );
}
