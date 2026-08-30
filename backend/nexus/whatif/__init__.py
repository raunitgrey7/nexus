"""What-If engine: scenario DSL, presets, parallel multi-strategy evaluation."""

from nexus.whatif.engine import WhatIfEngine
from nexus.whatif.presets import PRESETS, preset_by_id
from nexus.whatif.scenarios import describe_scenario, mutation_faults, scenario_faults

__all__ = [
    "PRESETS",
    "WhatIfEngine",
    "describe_scenario",
    "mutation_faults",
    "preset_by_id",
    "scenario_faults",
]
