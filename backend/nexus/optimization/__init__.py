"""Optimization engine: multi-objective scoring, batching, weighted-EDF sequencing, CP-SAT /
Hungarian / greedy / genetic assignment, congestion-aware routing policies and the ``optimized``
scheduling strategy."""

from nexus.optimization.assignment import (
    AssignmentProblem,
    AssignmentResult,
    PairEstimate,
    build_problem,
    solve,
    solve_cpsat,
    solve_greedy,
    solve_hungarian,
)
from nexus.optimization.batching import batch_summary, build_batches, order_zones, zone_hops
from nexus.optimization.constraints import (
    assignment_feasible,
    battery_requirement,
    trip_estimate,
    validate_tasks,
)
from nexus.optimization.engine import OptimizationEngine, PlanResult
from nexus.optimization.genetic import GeneticAllocator
from nexus.optimization.objective import (
    DEFAULT_WEIGHTS,
    ObjectiveWeights,
    compare_scores,
    score_breakdown,
    score_kpis,
)
from nexus.optimization.quick_compare import compare_strategies, run_strategy
from nexus.optimization.routing import CellCost, RoutingPolicy
from nexus.optimization.scheduling import order_urgency, priority_weight, sequence_orders, weighted_deadline
from nexus.optimization.strategy import OptimizedGreedyStrategy, OptimizedStrategy

__all__ = [
    "DEFAULT_WEIGHTS",
    "AssignmentProblem",
    "AssignmentResult",
    "CellCost",
    "GeneticAllocator",
    "ObjectiveWeights",
    "OptimizationEngine",
    "OptimizedGreedyStrategy",
    "OptimizedStrategy",
    "PairEstimate",
    "PlanResult",
    "RoutingPolicy",
    "assignment_feasible",
    "batch_summary",
    "battery_requirement",
    "build_batches",
    "build_problem",
    "compare_scores",
    "compare_strategies",
    "order_urgency",
    "order_zones",
    "priority_weight",
    "run_strategy",
    "score_breakdown",
    "score_kpis",
    "sequence_orders",
    "solve",
    "solve_cpsat",
    "solve_greedy",
    "solve_hungarian",
    "trip_estimate",
    "validate_tasks",
    "weighted_deadline",
    "zone_hops",
]
