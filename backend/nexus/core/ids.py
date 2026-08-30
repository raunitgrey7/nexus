"""Deterministic identifier generation.

Identifiers are derived from per-prefix counters that live inside the world state, so a forked
world produces exactly the same ids as the live world would have. Human-readable ids (``R07``,
``ORD-000123``) matter: agents, logs and the UI all talk about entities by id.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class IdGen:
    counters: dict[str, int] = field(default_factory=dict)

    def next(self, prefix: str, width: int = 6) -> str:
        n = self.counters.get(prefix, 0) + 1
        self.counters[prefix] = n
        return f"{prefix}-{n:0{width}d}"

    def next_short(self, prefix: str, width: int = 2) -> str:
        n = self.counters.get(prefix, 0) + 1
        self.counters[prefix] = n
        return f"{prefix}{n:0{width}d}"

    def peek(self, prefix: str) -> int:
        return self.counters.get(prefix, 0)

    def copy(self) -> IdGen:
        return IdGen(dict(self.counters))


def zone_letter(index: int) -> str:
    """0 → A, 25 → Z, 26 → AA ..."""
    letters = ""
    index += 1
    while index > 0:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters
