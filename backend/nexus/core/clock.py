"""Deterministic simulation clock.

Simulated time is an integer tick counter. Wall-clock time never enters the engine, which is what
makes forks, replays and benchmarks bit-for-bit reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

DEFAULT_EPOCH = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)  # a Monday, 08:00 — start of the day shift


@dataclass(slots=True)
class SimClock:
    tick: int = 0
    tick_seconds: int = 1
    epoch: datetime = field(default=DEFAULT_EPOCH)

    def now(self) -> datetime:
        return self.epoch + timedelta(seconds=self.tick * self.tick_seconds)

    def advance(self, ticks: int = 1) -> int:
        self.tick += ticks
        return self.tick

    @property
    def seconds(self) -> int:
        return self.tick * self.tick_seconds

    @property
    def minutes(self) -> float:
        return self.seconds / 60.0

    @property
    def hours(self) -> float:
        return self.seconds / 3600.0

    def hour_of_day(self) -> int:
        return self.now().hour

    def ticks_for_minutes(self, minutes: float) -> int:
        return round(minutes * 60 / self.tick_seconds)

    def copy(self) -> SimClock:
        return SimClock(tick=self.tick, tick_seconds=self.tick_seconds, epoch=self.epoch)

    def to_dict(self) -> dict:
        return {"tick": self.tick, "tick_seconds": self.tick_seconds, "sim_time": self.now().isoformat()}
