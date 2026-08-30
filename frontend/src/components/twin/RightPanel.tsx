"use client";

import { useShallow } from "zustand/react/shallow";
import { Tabs } from "@/components/ui/Table";
import { useTwinStore, type PanelTab } from "@/store/twinStore";
import { EventFeed } from "./EventFeed";
import { OrdersTable } from "./OrdersTable";
import { RobotsTable } from "./RobotsTable";
import { ZonesTable } from "./ZonesTable";

export function RightPanel() {
  const { tab, setTab, robotCount, orderCount, zoneCount, eventCount } = useTwinStore(
    useShallow((s) => ({
      tab: s.panelTab,
      setTab: s.setPanelTab,
      robotCount: s.robotIds.length,
      orderCount: s.world?.orders.length ?? 0,
      zoneCount: s.world?.zones.length ?? 0,
      eventCount: s.events.length,
    })),
  );
  const tabs: { id: PanelTab; label: string; count?: number }[] = [
    { id: "events", label: "Events", count: eventCount },
    { id: "robots", label: "Robots", count: robotCount },
    { id: "orders", label: "Orders", count: orderCount },
    { id: "zones", label: "Zones", count: zoneCount },
  ];
  return (
    <aside className="flex h-full min-h-0 w-[360px] shrink-0 flex-col border-l border-border bg-panel">
      <Tabs tabs={tabs} value={tab} onChange={setTab} />
      <div className="min-h-0 flex-1">
        {tab === "events" && <EventFeed />}
        {tab === "robots" && <RobotsTable />}
        {tab === "orders" && <OrdersTable />}
        {tab === "zones" && <ZonesTable />}
      </div>
    </aside>
  );
}
