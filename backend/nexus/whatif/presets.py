"""Canned what-if questions shown in the UI and understood by the NL console."""

from __future__ import annotations

from nexus.api.schemas import MutationModel, ScenarioModel, WhatIfPreset


def _s(name: str, description: str, *mutations: MutationModel) -> ScenarioModel:
    return ScenarioModel(name=name, description=description, mutations=list(mutations))


PRESETS: list[WhatIfPreset] = [
    WhatIfPreset(
        id="demand-plus-40",
        name="Demand +40%",
        question="What happens if order volume increases by 40%?",
        description="Sustained demand increase — tests capacity headroom and the value of batching.",
        scenario=_s(
            "Demand +40%",
            "Order arrival rate ×1.4",
            MutationModel(type="DEMAND_MULTIPLIER", params={"multiplier": 1.4}),
        ),
    ),
    WhatIfPreset(
        id="demand-double-30",
        name="Demand doubles for 30 min",
        question="What if demand doubles for the next 30 minutes?",
        description="A short burst — tests backlog recovery.",
        scenario=_s(
            "Demand burst ×2",
            "30-minute burst",
            MutationModel(type="DEMAND_BURST", params={"multiplier": 2.0, "duration_min": 30}),
        ),
    ),
    WhatIfPreset(
        id="r07-failure",
        name="Robot R07 fails",
        question="What if robot R07 fails right now?",
        description="The signature incident: one robot down, tasks released, congestion shifts.",
        scenario=_s(
            "R07 failure",
            "Motor fault, 45 min recovery",
            MutationModel(
                type="ROBOT_FAILURE",
                params={"robot_ids": ["R07"], "cause": "motor_fault", "recovery_min": 45},
            ),
        ),
    ),
    WhatIfPreset(
        id="remove-2-robots",
        name="Remove 2 robots",
        question="What if we remove two robots?",
        description="Fleet reduction — is the operation still within SLA?",
        scenario=_s(
            "Remove 2 robots",
            "Two robots withdrawn",
            MutationModel(type="REMOVE_ROBOTS", params={"count": 2}),
        ),
    ),
    WhatIfPreset(
        id="add-2-robots",
        name="Add 2 robots",
        question="What if we add two robots?",
        description="Fleet expansion — the marginal value of hardware.",
        scenario=_s(
            "Add 2 robots",
            "Two robots added at the charging bay",
            MutationModel(type="ADD_ROBOTS", params={"count": 2}),
        ),
    ),
    WhatIfPreset(
        id="zone-b-closed",
        name="Zone B inaccessible",
        question="What if Zone B is inaccessible for an hour?",
        description="A storage zone closes (spill, maintenance) — orders needing it stall unless inventory is elsewhere.",
        scenario=_s(
            "Zone B closed",
            "Closed for 60 min",
            MutationModel(type="CLOSE_ZONE", params={"zone_id": "B", "reopen_min": 60}),
        ),
    ),
    WhatIfPreset(
        id="dock-d2-closed",
        name="Loading dock D2 closes",
        question="What if loading dock 2 closes?",
        description="Deliveries rebalance to the remaining docks.",
        scenario=_s("Dock D2 closed", "", MutationModel(type="CLOSE_DOCK", params={"dock_id": "D2"})),
    ),
    WhatIfPreset(
        id="chargers-half",
        name="Charging capacity halved",
        question="What if charging capacity is reduced by half?",
        description="Battery becomes the binding constraint.",
        scenario=_s("Chargers −50%", "", MutationModel(type="DISABLE_CHARGERS", params={"count": 2})),
    ),
    WhatIfPreset(
        id="aisle-block-c",
        name="Aisle blocked in Zone C",
        question="What if an aisle in Zone C is blocked for 30 minutes?",
        description="Routing detours and replanning under a physical obstruction.",
        scenario=_s(
            "Aisle blocked (C)",
            "",
            MutationModel(type="BLOCK_AISLE", params={"zone_id": "C", "aisles": 1, "clear_min": 30}),
        ),
    ),
    WhatIfPreset(
        id="reposition-c-to-b",
        name="Move hot inventory C → B",
        question="What if we move the hottest SKUs from Zone C to Zone B?",
        description="Inventory slotting as a congestion lever.",
        scenario=_s(
            "Reposition C→B",
            "",
            MutationModel(
                type="MOVE_INVENTORY", params={"from_zone": "C", "to_zone": "B", "skus": 6, "units": 40}
            ),
        ),
    ),
    WhatIfPreset(
        id="batching-3",
        name="Enable 3-order batching",
        question="What if robots batch three orders per trip?",
        description="Throughput lever without new hardware.",
        scenario=_s("Batching 3/trip", "", MutationModel(type="SET_BATCHING", params={"orders_per_trip": 3})),
    ),
    WhatIfPreset(
        id="failure-plus-demand",
        name="R07 fails during a demand spike",
        question="What if R07 fails while demand is up 30%?",
        description="Compound incident.",
        scenario=_s(
            "R07 failure + demand +30%",
            "",
            MutationModel(type="DEMAND_MULTIPLIER", params={"multiplier": 1.3}),
            MutationModel(
                type="ROBOT_FAILURE",
                params={"robot_ids": ["R07"], "cause": "motor_fault", "recovery_min": 45},
                at_min=5,
            ),
        ),
    ),
]


def preset_by_id(preset_id: str) -> WhatIfPreset | None:
    return next((p for p in PRESETS if p.id == preset_id), None)
