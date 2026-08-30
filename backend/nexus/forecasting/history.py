"""Bounded operational history sampled from the live (or a forked) world.

The recorder is an engine hook (``engine.hooks.append(recorder.hook)``). It samples every
``sample_every_ticks`` (one simulated minute by default) and keeps three bounded buffers:

* ``samples`` — compact scalar samples (orders, backlog, breach, congestion, utilization, battery).
* ``zone_occupancy`` — the last ``zone_window`` per-zone occupancy dicts (congestion trend).
* ``robot_battery`` — the last ``battery_window`` per-robot battery dicts (drain-rate estimates).

The projected breach uses exactly the KPI definition in :mod:`nexus.simulation.metrics`
(``(late + overdue_open) / (delivered + open)``) but is computed from the running stats and the open
orders only, which is O(open orders) instead of O(all orders). Everything is picklable so simulation
worlds can carry a forked history.
"""

from __future__ import annotations

import copy
from collections import deque
from dataclasses import asdict, dataclass, fields
from typing import TYPE_CHECKING, Any

from nexus.api.schemas import TimelinePoint
from nexus.twin.entities import OrderStatus

if TYPE_CHECKING:
    from nexus.simulation.engine import SimulationEngine
    from nexus.twin.world import WorldState


@dataclass(slots=True)
class Sample:
    tick: int
    orders_created: int  # cumulative
    orders_delta: int  # new orders since the previous sample
    delivered: int  # cumulative
    open: int
    pending: int
    breach_projected: float
    congestion: float
    utilization: float  # productive / operational robot-ticks since the previous sample
    mean_battery: float
    robots_operational: int


SERIES_NAMES: tuple[str, ...] = tuple(f.name for f in fields(Sample))


class HistoryRecorder:
    def __init__(
        self,
        sample_every_ticks: int = 60,
        max_samples: int = 2880,
        zone_window: int = 60,
        battery_window: int = 30,
    ) -> None:
        self.sample_every_ticks = max(1, int(sample_every_ticks))
        self.max_samples = max(1, int(max_samples))
        self.zone_window = max(1, int(zone_window))
        self.battery_window = max(2, int(battery_window))
        self.tick_seconds = 1
        self.samples: deque[Sample] = deque(maxlen=self.max_samples)
        self.zone_occupancy: deque[tuple[int, dict[str, int]]] = deque(maxlen=self.zone_window)
        self.robot_battery: deque[tuple[int, dict[str, float]]] = deque(maxlen=self.battery_window)
        self._last_tick: int | None = None
        self._last_created = 0
        self._last_productive = 0
        self._last_operational = 0
        self._last_utilization = 0.0

    # ---- recording -----------------------------------------------------------------------------
    def hook(self, engine: SimulationEngine) -> None:
        """Engine hook: sample when the tick is a multiple of ``sample_every_ticks``."""
        self.maybe_record(engine.world)

    def maybe_record(self, world: WorldState) -> bool:
        tick = world.clock.tick
        if tick % self.sample_every_ticks != 0 or tick == self._last_tick:
            return False
        self.record(world)
        return True

    def record(self, world: WorldState) -> Sample:
        tick = world.clock.tick
        self.tick_seconds = world.clock.tick_seconds
        st = world.stats
        open_orders = world.open_orders()
        pending = 0
        overdue = 0
        for order in open_orders:
            if order.status == OrderStatus.PENDING:
                pending += 1
            if tick > order.deadline_tick:
                overdue += 1
        denom = st.orders_delivered + len(open_orders)
        breach = (st.orders_late + overdue) / denom if denom else 0.0
        d_prod = st.productive_robot_ticks - self._last_productive
        d_oper = st.operational_robot_ticks - self._last_operational
        utilization = d_prod / d_oper if d_oper > 0 else self._last_utilization
        robots = list(world.robots.values())
        operational = [r for r in robots if r.status.operational]
        pool = operational or robots
        mean_battery = sum(r.battery for r in pool) / len(pool) if pool else 0.0
        sample = Sample(
            tick=tick,
            orders_created=st.orders_created,
            orders_delta=st.orders_created - self._last_created,
            delivered=st.orders_delivered,
            open=len(open_orders),
            pending=pending,
            breach_projected=round(breach, 5),
            congestion=float(world.congestion_total()),
            utilization=round(min(1.0, max(0.0, utilization)), 5),
            mean_battery=round(mean_battery, 3),
            robots_operational=len(operational),
        )
        self.samples.append(sample)
        self.zone_occupancy.append((tick, {z: n for z, n in world.zone_occupancy.items() if n}))
        self.robot_battery.append((tick, {r.id: round(r.battery, 3) for r in robots}))
        self._last_tick = tick
        self._last_created = st.orders_created
        self._last_productive = st.productive_robot_ticks
        self._last_operational = st.operational_robot_ticks
        self._last_utilization = sample.utilization
        return sample

    # ---- access --------------------------------------------------------------------------------
    def __len__(self) -> int:
        return len(self.samples)

    def latest(self) -> Sample | None:
        return self.samples[-1] if self.samples else None

    def ticks(self) -> list[int]:
        return [s.tick for s in self.samples]

    def series(self, name: str) -> list[float]:
        if name not in SERIES_NAMES:
            raise KeyError(f"unknown series {name!r}; choose one of {SERIES_NAMES}")
        return [float(getattr(s, name)) for s in self.samples]

    def minutes_per_sample(self) -> float:
        return self.sample_every_ticks * self.tick_seconds / 60.0

    def per_minute_series(self) -> list[float]:
        """Order arrivals per simulated minute, one value per sample interval (first sample excluded)."""
        mins = self.minutes_per_sample()
        return [s.orders_delta / mins for s in list(self.samples)[1:]]

    def arrival_rate_per_min(self, window: int = 15) -> float:
        """Observed arrival rate (orders / simulated minute) over the last ``window`` minutes."""
        samples = list(self.samples)
        if len(samples) < 2:
            return 0.0
        last = samples[-1]
        cutoff = last.tick - window * 60.0 / self.tick_seconds
        idx = 0
        for i, s in enumerate(samples):
            if s.tick >= cutoff:
                idx = i
                break
        idx = min(idx, len(samples) - 2)
        first = samples[idx]
        span_min = (last.tick - first.tick) * self.tick_seconds / 60.0
        if span_min <= 0:
            return 0.0
        total = sum(s.orders_delta for s in samples[idx + 1 :])
        return total / span_min

    def zone_series(self, zone_id: str, window_min: float | None = None) -> list[tuple[int, int]]:
        out = [(tick, occ.get(zone_id, 0)) for tick, occ in self.zone_occupancy]
        if window_min is not None and out:
            cutoff = out[-1][0] - window_min * 60.0 / self.tick_seconds
            out = [p for p in out if p[0] >= cutoff]
        return out

    def robot_battery_series(self, robot_id: str) -> list[tuple[int, float]]:
        return [(tick, bat[robot_id]) for tick, bat in self.robot_battery if robot_id in bat]

    def timeline_points(self) -> list[TimelinePoint]:
        return [
            TimelinePoint(
                tick=s.tick,
                open=s.open,
                delivered=s.delivered,
                breach_projected=s.breach_projected,
                congestion=s.congestion,
                utilization=s.utilization,
            )
            for s in self.samples
        ]

    # ---- (de)serialisation ---------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_every_ticks": self.sample_every_ticks,
            "max_samples": self.max_samples,
            "zone_window": self.zone_window,
            "battery_window": self.battery_window,
            "tick_seconds": self.tick_seconds,
            "samples": [asdict(s) for s in self.samples],
            "zone_occupancy": [[tick, occ] for tick, occ in self.zone_occupancy],
            "robot_battery": [[tick, bat] for tick, bat in self.robot_battery],
            "last": {
                "tick": self._last_tick,
                "created": self._last_created,
                "productive": self._last_productive,
                "operational": self._last_operational,
                "utilization": self._last_utilization,
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HistoryRecorder:
        rec = cls(
            sample_every_ticks=int(data.get("sample_every_ticks", 60)),
            max_samples=int(data.get("max_samples", 2880)),
            zone_window=int(data.get("zone_window", 60)),
            battery_window=int(data.get("battery_window", 30)),
        )
        rec.tick_seconds = int(data.get("tick_seconds", 1))
        for s in data.get("samples", []):
            rec.samples.append(Sample(**s))
        for tick, occ in data.get("zone_occupancy", []):
            rec.zone_occupancy.append((int(tick), {k: int(v) for k, v in occ.items()}))
        for tick, bat in data.get("robot_battery", []):
            rec.robot_battery.append((int(tick), {k: float(v) for k, v in bat.items()}))
        last = data.get("last", {})
        rec._last_tick = last.get("tick")
        rec._last_created = int(last.get("created", 0))
        rec._last_productive = int(last.get("productive", 0))
        rec._last_operational = int(last.get("operational", 0))
        rec._last_utilization = float(last.get("utilization", 0.0))
        return rec

    def fork(self) -> HistoryRecorder:
        """Independent deep copy for a simulation world."""
        return copy.deepcopy(self)
