# Extending NEXUS to a new domain

The engine (events, simulation, optimization, forecasting, agents, what-if) is domain-agnostic: it knows about *shapes*
— zones, mobile agents with batteries, jobs with deadlines and pick/deliver locations, sinks, chargers, workers — not
about warehouses. A domain supplies a concrete world through the `DomainModel` protocol; the warehouse is the first
implementation. This document explains what is generic, what is warehouse-specific, how to add a domain, and sketches a
factory and a hospital.

## The protocol (`nexus/twin/domain.py`)

```python
@runtime_checkable
class DomainModel(Protocol):
    name: str
    scales: list[str]
    def build(self, scale: str = "small", seed: int = 42, **overrides) -> WorldState: ...
    def vocabulary(self) -> dict[str, str]: ...   # engine concept → domain wording
    def describe(self, world: WorldState) -> str: ...  # one-paragraph NL description for prompts
```

`DOMAINS = {"warehouse": WarehouseDomain()}` and `get_domain(name)` are the registry; `LiveRuntime` builds its world with
`get_domain("warehouse").build(scale, seed=seed)`. `WarehouseDomain.vocabulary()` maps `agent → robot`, `job → order`,
`resource → shelf`, `sink → loading dock`, `energy → battery`, `area → zone`, `operator → worker`.

## What is engine-agnostic

* `nexus/events`, `nexus/simulation` (kinematics, pathfinding, order/task lifecycle, KPIs), `nexus/optimization`
  (objective, batching, sequencing, assignment, routing), `nexus/forecasting`, `nexus/agents` (pipeline, validator,
  executor, simulator, risk, policy), `nexus/whatif` (engine), `nexus/api`, `nexus/runtime`, `nexus/persistence`.
* The entity shapes in `nexus/twin/entities.py`: a `Robot` is any mobile agent with a battery and capacity; an `Order`
  is any job with lines to collect and a deadline; a `Shelf` is any pick location with stock; a `LoadingDock` is any
  sink; a `Zone` is any area with a capacity.

## What is warehouse-specific (and where)

| Piece | Location | Notes |
|---|---|---|
| Layout generator, scales, zone naming (A…, C1…, CHG, DOCK) | `nexus/twin/layout.py` | replace with your layout |
| Demand profile defaults (10-hour window, priority mix, SLAs) | `entities.py: DemandProfile`, `SimConfig.sla_minutes` | override per domain |
| Fault presets (R07, D2, Zone C …) | `nexus/runtime/live.py: FAULT_PRESETS` | ids assume the warehouse layout |
| What-if presets and NL defaults ("R07 fails") | `nexus/whatif/presets.py`, `nexus/nlq/router.py` | same |
| Planner playbooks and SOPs (corridors, docks, loaders) | `nexus/agents/planner.py`, `nexus/llm/rag.py` | write domain playbooks + SOPs |
| Explanation phrases | `nexus/agents/explain.py: ACTION_PHRASES`, `nlq/explain.py: CAUSE_LABEL` | wording only |
| UI labels and 3D glyphs | `frontend/` | rendering only |

## Adding a domain — steps

1. **Model the space.** Build a `GridMap` (cell types + zones) and `Zone`s; every pick location needs a walkable
   `access_cell`; sinks and chargers are walkable cells.
2. **Populate entities.** Create `Shelf`/`ChargingStation`/`LoadingDock`/`Worker`/`Robot` objects and the SKU index;
   choose a `DemandProfile` (arrival rate, hourly shape, priority mix) and `SimConfig` (speeds, action durations, SLAs).
3. **Implement `DomainModel`** with `build`, `vocabulary`, `describe`, and register it in `DOMAINS`.
4. **Playbooks + SOPs.** Add domain playbooks to `PlannerAgent.heuristic_plans` (or subclass it) and SOP documents to
   `nexus/llm/rag.py` so the LLM's proposals follow house rules.
5. **Presets.** Add fault presets and what-if presets with the domain's ids; adjust the NL defaults.
6. **Validate.** Run the determinism gates (`scripts/smoke_sim.py`), the benchmark on your world, and calibrate demand so
   the baseline runs near capacity (see `scripts/calibrate.py`).

## Sketch: factory

| Engine concept | Factory meaning |
|---|---|
| zones | workcells and buffer areas; corridors = AGV lanes |
| robots | AGVs / tuggers moving WIP between cells |
| shelves + SKUs | supermarket racks / kitting locations holding parts |
| orders | transport requests (kits) with takt-time deadlines |
| loading docks | line-side delivery points |
| chargers | AGV charging spots |
| workers | line operators (a missing operator slows a delivery point) |
| incidents | AGV fault, lane blocked, cell stoppage (= zone closed), demand surge (= takt change) |

The optimization objective needs takt lateness weighted higher than distance; the congestion model maps directly
(lanes over capacity slow AGVs).

## Sketch: hospital

| Engine concept | Hospital meaning |
|---|---|
| zones | wards, imaging, pharmacy, labs; corridors and lifts |
| robots | porters or transport robots (beds, samples, meds) |
| shelves + SKUs | pharmacy/supply locations |
| orders | transport requests (patient moves, sample runs) with clinical priority (CRITICAL = emergency) |
| loading docks | destination wards / labs |
| chargers | robot docks or porter breaks |
| workers | nurses/porters whose absence slows hand-over |
| incidents | lift out of service (= corridor closed), ward isolation (= zone closed), surge (= demand burst) |

Here the priority weights (`nexus/optimization/scheduling.py: PRIORITY_WEIGHT`) would be steeper and the approval
policy would default to `human` for anything touching a critical order.

## Other candidates

Airport (baggage tugs and gates), data centre (technician dispatch and spare-part carts), smart building (cleaning and
delivery robots), fleets and supply chains (vehicles and hubs; cells become road segments) all fit the same shapes; the
difference is the layout, the demand model and the playbooks.
