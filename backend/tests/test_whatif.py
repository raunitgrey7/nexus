import time

from nexus.api.schemas import MutationModel, ScenarioModel, WhatIfRequest
from nexus.events.types import EventType
from nexus.simulation.engine import SimulationEngine
from nexus.simulation.faults import FaultInjector
from nexus.simulation.strategies import make_strategy
from nexus.twin import build_world, spec_for
from nexus.whatif import PRESETS, WhatIfEngine, describe_scenario, preset_by_id, scenario_faults


def _engine(scale="tiny", seed=4, ticks=600):
    world = build_world(spec_for(scale, seed=seed))
    eng = SimulationEngine(world, make_strategy("optimized"), fault_injector=FaultInjector(spontaneous=False))
    eng.run(ticks)
    return eng


def test_every_preset_translates_to_faults(small_world):
    for preset in PRESETS:
        faults = scenario_faults(small_world, preset.scenario, 100)
        assert faults, preset.id
        assert all(isinstance(f.type, EventType) and f.tick >= 100 for f in faults)
        assert describe_scenario(preset.scenario)
    assert preset_by_id("r07-failure") is not None and preset_by_id("nope") is None


def test_mutation_specifics(small_world):
    w = small_world
    sc = ScenarioModel(
        name="mix",
        mutations=[
            MutationModel(type="REMOVE_ROBOTS", params={"count": 2}),
            MutationModel(type="ADD_ROBOTS", params={"count": 1}, at_min=5),
            MutationModel(type="BLOCK_AISLE", params={"zone_id": "C", "aisles": 1, "clear_min": 10}),
            MutationModel(type="DEMAND_BURST", params={"multiplier": 2.0, "duration_min": 15}),
            MutationModel(type="SET_SLA", params={"NORMAL": 8}),
            MutationModel(
                type="MOVE_INVENTORY", params={"from_zone": "C", "to_zone": "B", "skus": 3, "units": 20}
            ),
        ],
    )
    faults = scenario_faults(w, sc, 0)
    types = [f.type for f in faults]
    assert types.count(EventType.ROBOT_REMOVED) == 2
    assert (
        EventType.ROBOT_ADDED in types
        and next(f for f in faults if f.type == EventType.ROBOT_ADDED).tick == 300
    )
    assert EventType.AISLE_BLOCKED in types and EventType.AISLE_CLEARED in types
    assert EventType.DEMAND_CHANGED in types and EventType.CONFIG_CHANGED in types
    assert EventType.INVENTORY_MOVED in types


def test_whatif_engine_compares_strategies():
    eng = _engine()
    wie = WhatIfEngine(lambda: eng, workers=1)
    req = WhatIfRequest(
        scenario=preset_by_id("remove-2-robots").scenario,
        strategies=["baseline", "optimized"],
        horizon_min=10,
        seeds=1,
    )
    before = eng.world.digest()
    result = wie.run(req)
    assert eng.world.digest() == before  # the live world is untouched
    assert result.status == "done" and len(result.runs) == 2 and result.reference is not None
    assert result.best_strategy in ("baseline", "optimized")
    assert [r["strategy"] for r in result.comparison] == [
        r["strategy"] for r in sorted(result.comparison, key=lambda r: r["score"])
    ]
    assert "Remove 2 robots" in result.narrative
    assert all(len(run.timeline) >= 2 for run in result.runs)
    assert all(run.kpis.robots_total == 2 for run in result.runs)  # tiny has 4 robots, two removed
    assert result.reference.kpis.robots_total == 4


def test_whatif_async_submit():
    eng = _engine()
    wie = WhatIfEngine(lambda: eng, workers=1)
    placeholder = wie.submit(
        WhatIfRequest(
            scenario=preset_by_id("demand-plus-40").scenario,
            strategies=["optimized"],
            horizon_min=5,
            seeds=2,
            include_current=False,
        )
    )
    assert placeholder.status in ("queued", "running")
    deadline = time.time() + 90
    while time.time() < deadline and wie.get(placeholder.id).status in ("queued", "running"):
        time.sleep(0.2)
    result = wie.get(placeholder.id)
    assert result.status == "done" and len(result.runs) == 2 and result.reference is None
    assert wie.history()[0].id == placeholder.id
