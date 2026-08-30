"""Fault injection: scheduled scenario faults and spontaneous (seeded) robot failures."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from nexus.events.types import EventType

if TYPE_CHECKING:
    from nexus.simulation.engine import SimulationEngine

FAILURE_CAUSES = [
    "motor_fault",
    "lidar_fault",
    "wheel_slip",
    "controller_reboot",
    "battery_cell_fault",
    "collision_stop",
]


@dataclass(slots=True)
class ScheduledFault:
    tick: int
    type: EventType
    entity_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    key: str | None = None
    origin: str = "scenario"

    def to_dict(self) -> dict[str, Any]:
        return {
            "tick": self.tick,
            "type": self.type.value,
            "entity_id": self.entity_id,
            "payload": self.payload,
            "key": self.key,
            "origin": self.origin,
        }


class FaultInjector:
    def __init__(self, scheduled: list[ScheduledFault] | None = None, spontaneous: bool = True) -> None:
        self.scheduled: list[ScheduledFault] = sorted(scheduled or [], key=lambda f: f.tick)
        self.spontaneous = spontaneous
        self._i = 0
        self.injected: list[str] = []

    def schedule(
        self,
        tick: int,
        type_: EventType,
        entity_id: str | None = None,
        payload: dict[str, Any] | None = None,
        key: str | None = None,
        origin: str = "scenario",
    ) -> ScheduledFault:
        fault = ScheduledFault(tick, type_, entity_id, payload or {}, key, origin)
        self.scheduled.append(fault)
        self.scheduled.sort(key=lambda f: f.tick)
        return fault

    def tick(self, engine: SimulationEngine) -> None:
        world = engine.world
        t = world.clock.tick
        while self._i < len(self.scheduled) and self.scheduled[self._i].tick <= t:
            f = self.scheduled[self._i]
            self._i += 1
            key = f.key or f"{f.origin}:{f.tick}:{f.type.value}:{f.entity_id or '-'}"
            ev = engine.inject(f.type, f.entity_id, dict(f.payload), origin=f.origin, key=key)
            if ev is not None:
                self.injected.append(ev.id)
        if not self.spontaneous:
            return
        rate = world.config.robot_failure_rate_per_hour
        if rate <= 0:
            return
        p = rate * world.clock.tick_seconds / 3600.0
        for rid in sorted(world.robots):
            robot = world.robots[rid]
            if not robot.status.operational:
                continue
            if world.rng.chance(p):
                cause = world.rng.choice(FAILURE_CAUSES)
                minutes = world.config.failure_recovery_minutes * world.rng.uniform(0.6, 1.6)
                engine.emit(
                    EventType.ROBOT_FAILURE,
                    rid,
                    {"cause": cause, "recovery_ticks": int(minutes * 60 / world.clock.tick_seconds)},
                )

    def remaining(self) -> list[ScheduledFault]:
        return self.scheduled[self._i :]
