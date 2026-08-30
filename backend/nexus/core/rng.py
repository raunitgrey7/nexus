"""Seeded random number generation with fork/restore support.

All stochastic behaviour in the engine (order arrivals, failures, pick durations) flows through a
single :class:`SeededRNG` owned by the world state. Forking a world copies the RNG state, so a
simulation world evolves exactly as the live world would have, until a decision diverges them.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from typing import Any, TypeVar

T = TypeVar("T")


class SeededRNG:
    __slots__ = ("_rng", "seed")

    def __init__(self, seed: int = 42, state: Any | None = None) -> None:
        self.seed = seed
        self._rng = random.Random(seed)
        if state is not None:
            self._rng.setstate(state)

    # ---- state management ---------------------------------------------------------------------
    def getstate(self) -> Any:
        return self._rng.getstate()

    def setstate(self, state: Any) -> None:
        self._rng.setstate(state)

    def fork(self) -> SeededRNG:
        return SeededRNG(self.seed, self._rng.getstate())

    def derive(self, salt: int) -> SeededRNG:
        """A new independent stream deterministically derived from this seed and ``salt``."""
        return SeededRNG((self.seed * 1_000_003 + salt) % (2**63 - 1))

    # ---- primitives ----------------------------------------------------------------------------
    def random(self) -> float:
        return self._rng.random()

    def uniform(self, a: float, b: float) -> float:
        return self._rng.uniform(a, b)

    def randint(self, a: int, b: int) -> int:
        return self._rng.randint(a, b)

    def gauss(self, mu: float, sigma: float) -> float:
        return self._rng.gauss(mu, sigma)

    def expovariate(self, lambd: float) -> float:
        return self._rng.expovariate(lambd)

    def choice(self, seq: Sequence[T]) -> T:
        return self._rng.choice(seq)

    def choices(self, population: Sequence[T], weights: Sequence[float] | None = None, k: int = 1) -> list[T]:
        return self._rng.choices(population, weights=weights, k=k)

    def sample(self, population: Sequence[T], k: int) -> list[T]:
        return self._rng.sample(population, k)

    def shuffle(self, seq: list[Any]) -> None:
        self._rng.shuffle(seq)

    def chance(self, p: float) -> bool:
        return self._rng.random() < p

    def poisson(self, lam: float) -> int:
        """Poisson-distributed integer. Knuth for small λ, normal approximation for large λ."""
        if lam <= 0:
            return 0
        if lam < 30:
            limit = math.exp(-lam)
            k = 0
            p = 1.0
            while True:
                p *= self._rng.random()
                if p <= limit:
                    return k
                k += 1
        value = round(self._rng.gauss(lam, math.sqrt(lam)))
        return max(0, value)

    def weighted_index(self, weights: Sequence[float]) -> int:
        total = sum(weights)
        r = self._rng.random() * total
        acc = 0.0
        for i, w in enumerate(weights):
            acc += w
            if r < acc:
                return i
        return len(weights) - 1
