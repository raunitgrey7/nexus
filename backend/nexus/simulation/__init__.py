"""Deterministic simulation engine: kinematics, pathfinding, orders, faults, KPIs, strategies."""

from nexus.simulation.engine import SimulationEngine
from nexus.simulation.faults import FaultInjector, ScheduledFault
from nexus.simulation.metrics import KPIs, compute_kpis, kpi_delta
from nexus.simulation.order_generator import OrderGenerator
from nexus.simulation.pathfinding import Pathfinder
from nexus.simulation.strategies import STRATEGIES, GreedyStrategy, Strategy, make_strategy, register_strategy

__all__ = [
    "STRATEGIES",
    "FaultInjector",
    "GreedyStrategy",
    "KPIs",
    "OrderGenerator",
    "Pathfinder",
    "ScheduledFault",
    "SimulationEngine",
    "Strategy",
    "compute_kpis",
    "kpi_delta",
    "make_strategy",
    "register_strategy",
]
