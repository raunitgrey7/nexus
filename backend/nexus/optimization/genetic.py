"""Genetic allocator.

An evolutionary alternative to the exact solvers that doubles as a *candidate generator*: the final
population contains many distinct, feasible allocations ranked by objective, which the agents use
to present "we evaluated N reallocations" and to diversify the plans that get simulated.

Encoding: one gene per batch holding a robot index (or −1 = unassigned). A repair step keeps every
chromosome feasible (each robot at most once, only feasible pairs) so crossover/mutation never
produce invalid allocations. The population is seeded with the greedy allocation and the elite is
polished with a pairwise swap / reassign local search each generation (memetic GA), so the result
is never worse than greedy and is usually within a few percent of the CP-SAT optimum.
Fitness = :meth:`AssignmentProblem.objective` (lower is better). Deterministic for a given ``seed``.
"""

from __future__ import annotations

import random
import time
from typing import Any

from nexus.optimization.assignment import INF, AssignmentProblem, AssignmentResult

Chromosome = list[int]


class GeneticAllocator:
    def __init__(
        self,
        problem: AssignmentProblem,
        population: int = 40,
        generations: int = 60,
        seed: int = 42,
        mutation_rate: float = 0.15,
        elite: int = 4,
    ) -> None:
        self.problem = problem
        self.population_size = max(4, population)
        self.generations = max(1, generations)
        self.rng = random.Random(seed)
        self.mutation_rate = mutation_rate
        self.elite = max(1, min(elite, self.population_size // 2))
        self.evaluations = 0
        self.history: list[float] = []
        self.population: list[tuple[float, Chromosome]] = []
        self._feasible_robots: list[list[int]] = [
            [ri for ri in range(problem.n_robots) if problem.cost[ri][bi] < INF]
            for bi in range(problem.n_batches)
        ]

    # ---- chromosome helpers --------------------------------------------------------------------
    def _cost(self, ri: int, bi: int) -> float:
        return self.problem.cost[ri][bi]

    def _repair(self, chrom: Chromosome) -> Chromosome:
        problem = self.problem
        chrom = list(chrom)
        owner: dict[int, int] = {}
        for bi, ri in enumerate(chrom):
            if ri < 0:
                continue
            if self._cost(ri, bi) >= INF:
                chrom[bi] = -1
                continue
            if ri in owner:
                other = owner[ri]
                if self._cost(ri, bi) < self._cost(ri, other):
                    chrom[other] = -1
                    owner[ri] = bi
                else:
                    chrom[bi] = -1
            else:
                owner[ri] = bi
        used = set(owner)
        for bi in range(problem.n_batches):
            if chrom[bi] >= 0:
                continue
            best = -1
            best_c = INF
            for ri in self._feasible_robots[bi]:
                if ri in used:
                    continue
                c = self._cost(ri, bi)
                if c < best_c:
                    best, best_c = ri, c
            if best >= 0:
                chrom[bi] = best
                used.add(best)
        return chrom

    def _random(self) -> Chromosome:
        chrom = [-1] * self.problem.n_batches
        order = list(range(self.problem.n_batches))
        self.rng.shuffle(order)
        used: set[int] = set()
        for bi in order:
            options = [ri for ri in self._feasible_robots[bi] if ri not in used]
            if options and self.rng.random() < 0.9:
                ri = self.rng.choice(options)
                chrom[bi] = ri
                used.add(ri)
        return self._repair(chrom)

    def _pairs(self, chrom: Chromosome) -> list[tuple[str, int]]:
        return sorted((self.problem.robot_ids[ri], bi) for bi, ri in enumerate(chrom) if ri >= 0)

    def _fitness(self, chrom: Chromosome) -> float:
        self.evaluations += 1
        return self.problem.objective(self._pairs(chrom))

    def _crossover(self, a: Chromosome, b: Chromosome) -> Chromosome:
        child = [a[i] if self.rng.random() < 0.5 else b[i] for i in range(len(a))]
        return self._repair(child)

    def _mutate(self, chrom: Chromosome) -> Chromosome:
        chrom = list(chrom)
        n = len(chrom)
        if n == 0:
            return chrom
        if self.rng.random() < 0.5 and n > 1:
            i, j = self.rng.sample(range(n), 2)
            chrom[i], chrom[j] = chrom[j], chrom[i]
        else:
            bi = self.rng.randrange(n)
            options = self._feasible_robots[bi]
            chrom[bi] = self.rng.choice(options) if options else -1
        return self._repair(chrom)

    def _tournament(self, k: int = 3) -> Chromosome:
        contenders = [self.population[self.rng.randrange(len(self.population))] for _ in range(k)]
        return min(contenders, key=lambda t: t[0])[1]

    def _greedy_seed(self) -> Chromosome:
        """Cheapest-edge-first allocation used to seed the population (guarantees GA ≤ greedy)."""
        edges = sorted(
            (c, ri, bi) for ri, row in enumerate(self.problem.cost) for bi, c in enumerate(row) if c < INF
        )
        chrom = [-1] * self.problem.n_batches
        used: set[int] = set()
        for _c, ri, bi in edges:
            if ri in used or chrom[bi] >= 0:
                continue
            chrom[bi] = ri
            used.add(ri)
        return chrom

    def _local_search(self, chrom: Chromosome, max_passes: int = 3) -> tuple[float, Chromosome]:
        """Pairwise swap / reassign improvement (memetic step) applied to elite chromosomes."""
        best = list(chrom)
        best_fit = self._fitness(best)
        n = len(best)
        used = {ri for ri in best if ri >= 0}
        free = [ri for ri in range(self.problem.n_robots) if ri not in used]
        for _ in range(max_passes):
            improved = False
            for i in range(n):
                # try moving batch i to a free robot
                for ri in free:
                    if self._cost(ri, i) >= INF:
                        continue
                    cand = list(best)
                    cand[i] = ri
                    fit = self._fitness(cand)
                    if fit < best_fit - 1e-9:
                        best, best_fit, improved = cand, fit, True
                        used = {r for r in best if r >= 0}
                        free = [r for r in range(self.problem.n_robots) if r not in used]
                        break
                # try swapping robots between batches i and j
                for j in range(i + 1, n):
                    if best[i] == best[j]:
                        continue
                    cand = list(best)
                    cand[i], cand[j] = cand[j], cand[i]
                    if (cand[i] >= 0 and self._cost(cand[i], i) >= INF) or (
                        cand[j] >= 0 and self._cost(cand[j], j) >= INF
                    ):
                        continue
                    fit = self._fitness(cand)
                    if fit < best_fit - 1e-9:
                        best, best_fit, improved = cand, fit, True
            if not improved:
                break
        return best_fit, best

    # ---- evolution -----------------------------------------------------------------------------
    def run(self) -> list[tuple[float, Chromosome]]:
        if self.problem.n_batches == 0 or self.problem.n_robots == 0:
            self.population = []
            return self.population
        pop = [self._greedy_seed()] + [self._random() for _ in range(self.population_size - 1)]
        self.population = sorted(((self._fitness(c), c) for c in pop), key=lambda t: (t[0], t[1]))
        self.history = [self.population[0][0]]
        for _ in range(self.generations):
            elite_fit, elite = self._local_search(self.population[0][1])
            nxt: list[tuple[float, Chromosome]] = [(elite_fit, elite), *self.population[1 : self.elite]]
            while len(nxt) < self.population_size:
                child = self._crossover(self._tournament(), self._tournament())
                if self.rng.random() < self.mutation_rate:
                    child = self._mutate(child)
                nxt.append((self._fitness(child), child))
            self.population = sorted(nxt, key=lambda t: (t[0], t[1]))
            self.history.append(self.population[0][0])
        return self.population

    def solve(self) -> AssignmentResult:
        t0 = time.perf_counter()
        self.run()
        if not self.population:
            return AssignmentResult(
                [], "ga", 0.0, self.problem.objective([]), self.problem.evaluated, 0, "empty"
            )
        best_obj, best = self.population[0]
        pairs = self._pairs(best)
        return AssignmentResult(
            pairs, "ga", (time.perf_counter() - t0) * 1000, best_obj, self.evaluations, len(pairs)
        )

    def top_k(self, k: int = 5) -> list[tuple[list[tuple[str, int]], float]]:
        """The ``k`` best *distinct* allocations of the final population (runs the GA if needed)."""
        if not self.population:
            self.run()
        seen: set[tuple[tuple[str, int], ...]] = set()
        out: list[tuple[list[tuple[str, int]], float]] = []
        for obj, chrom in self.population:
            pairs = self._pairs(chrom)
            key = tuple(pairs)
            if key in seen:
                continue
            seen.add(key)
            out.append((pairs, obj))
            if len(out) >= k:
                break
        return out

    def describe(self) -> dict[str, Any]:
        return {
            "population": self.population_size,
            "generations": self.generations,
            "evaluations": self.evaluations,
            "best": self.history[-1] if self.history else None,
            "improvement": round(self.history[0] - self.history[-1], 4) if len(self.history) > 1 else 0.0,
        }
