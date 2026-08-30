import pytest

from nexus.simulation.engine import SimulationEngine
from nexus.simulation.faults import FaultInjector
from nexus.simulation.strategies import GreedyStrategy
from nexus.twin import build_world, spec_for


@pytest.fixture
def tiny_world():
    return build_world(spec_for("tiny", seed=7))


@pytest.fixture
def small_world():
    return build_world(spec_for("small", seed=42))


@pytest.fixture
def tiny_engine(tiny_world):
    return SimulationEngine(tiny_world, GreedyStrategy(), fault_injector=FaultInjector(spontaneous=False))


def make_engine(scale: str = "tiny", seed: int = 7, faults=None, strategy=None) -> SimulationEngine:
    world = build_world(spec_for(scale, seed=seed))
    return SimulationEngine(
        world, strategy or GreedyStrategy(), fault_injector=FaultInjector(faults or [], spontaneous=False)
    )
