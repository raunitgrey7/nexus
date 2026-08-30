"""Event vocabulary of the twin.

Every change to the world is an :class:`Event`. Events are:

* **typed** — :class:`EventType` is closed; unknown types are rejected by the reducer.
* **ordered** — ``seq`` is assigned by the store; ``tick`` is simulated time.
* **attributed** — ``origin`` says who produced it (``engine``, ``scenario``, ``agent``, ``user``).
  Non-engine events are the *external inputs* a replay must re-inject; engine events are regenerated
  deterministically.
* **optionally ephemeral** — high-frequency kinematic events are streamed to observers but not
  persisted (the engine reproduces them on replay).
* **idempotent** — an optional ``key`` lets the store drop duplicate deliveries of the same command.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class EventType(StrEnum):
    # orders
    ORDER_CREATED = "ORDER_CREATED"
    ORDER_ASSIGNED = "ORDER_ASSIGNED"
    ORDER_STARTED = "ORDER_STARTED"
    ORDER_DELIVERED = "ORDER_DELIVERED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    ORDER_REPRIORITIZED = "ORDER_REPRIORITIZED"
    # tasks
    TASK_CREATED = "TASK_CREATED"
    TASK_REASSIGNED = "TASK_REASSIGNED"
    TASK_CANCELLED = "TASK_CANCELLED"
    TASK_COMPLETED = "TASK_COMPLETED"
    # robots
    ROBOT_PATH_SET = "ROBOT_PATH_SET"
    ROBOT_MOVED = "ROBOT_MOVED"
    ROBOT_WAITING = "ROBOT_WAITING"
    ROBOT_STATUS_CHANGED = "ROBOT_STATUS_CHANGED"
    WAYPOINT_REACHED = "WAYPOINT_REACHED"
    BATTERY_UPDATED = "BATTERY_UPDATED"
    ITEM_PICKED = "ITEM_PICKED"
    ITEM_DROPPED = "ITEM_DROPPED"
    BATTERY_LOW = "BATTERY_LOW"
    CHARGING_STARTED = "CHARGING_STARTED"
    CHARGING_COMPLETED = "CHARGING_COMPLETED"
    ROBOT_FAILURE = "ROBOT_FAILURE"
    ROBOT_RECOVERED = "ROBOT_RECOVERED"
    ROBOT_ADDED = "ROBOT_ADDED"
    ROBOT_REMOVED = "ROBOT_REMOVED"
    # workers
    WORKER_DELAY = "WORKER_DELAY"
    WORKER_STATUS_CHANGED = "WORKER_STATUS_CHANGED"
    # infrastructure
    AISLE_BLOCKED = "AISLE_BLOCKED"
    AISLE_CLEARED = "AISLE_CLEARED"
    ZONE_CLOSED = "ZONE_CLOSED"
    ZONE_OPENED = "ZONE_OPENED"
    DOCK_CLOSED = "DOCK_CLOSED"
    DOCK_OPENED = "DOCK_OPENED"
    CHARGER_DISABLED = "CHARGER_DISABLED"
    CHARGER_ENABLED = "CHARGER_ENABLED"
    # inventory
    INVENTORY_MOVED = "INVENTORY_MOVED"
    INVENTORY_RESTOCKED = "INVENTORY_RESTOCKED"
    SHIPMENT_DEPARTED = "SHIPMENT_DEPARTED"
    # policy / demand
    DEMAND_CHANGED = "DEMAND_CHANGED"
    CONFIG_CHANGED = "CONFIG_CHANGED"
    # plans (informational — the actions they contain are separate events)
    PLAN_PROPOSED = "PLAN_PROPOSED"
    PLAN_APPROVED = "PLAN_APPROVED"
    PLAN_REJECTED = "PLAN_REJECTED"
    PLAN_EXECUTED = "PLAN_EXECUTED"
    # system
    TICK = "TICK"
    SNAPSHOT_TAKEN = "SNAPSHOT_TAKEN"


EPHEMERAL_TYPES = frozenset(
    {
        EventType.ROBOT_MOVED,
        EventType.ROBOT_WAITING,
        EventType.ROBOT_PATH_SET,
        EventType.BATTERY_UPDATED,
        EventType.TICK,
    }
)

# Events that carry meaning for operators (shown in the UI feed and used by the explain engine).
NOTABLE_TYPES = frozenset(
    {
        EventType.ROBOT_FAILURE,
        EventType.ROBOT_RECOVERED,
        EventType.BATTERY_LOW,
        EventType.AISLE_BLOCKED,
        EventType.AISLE_CLEARED,
        EventType.ZONE_CLOSED,
        EventType.ZONE_OPENED,
        EventType.DOCK_CLOSED,
        EventType.DOCK_OPENED,
        EventType.CHARGER_DISABLED,
        EventType.CHARGER_ENABLED,
        EventType.WORKER_DELAY,
        EventType.DEMAND_CHANGED,
        EventType.PLAN_PROPOSED,
        EventType.PLAN_APPROVED,
        EventType.PLAN_REJECTED,
        EventType.PLAN_EXECUTED,
        EventType.ORDER_CANCELLED,
        EventType.TASK_REASSIGNED,
        EventType.INVENTORY_MOVED,
    }
)


@dataclass(slots=True)
class Event:
    type: EventType
    tick: int
    entity_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    origin: str = "engine"
    seq: int = 0
    id: str = ""
    key: str | None = None  # idempotency key for externally produced commands
    cause: str | None = None  # id of the event / plan that caused this one
    ephemeral: bool = False

    @property
    def external(self) -> bool:
        return self.origin != "engine"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "seq": self.seq,
            "type": self.type.value,
            "tick": self.tick,
            "entity_id": self.entity_id,
            "payload": self.payload,
            "origin": self.origin,
            "key": self.key,
            "cause": self.cause,
            "ephemeral": self.ephemeral,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Event:
        return Event(
            type=EventType(d["type"]),
            tick=int(d["tick"]),
            entity_id=d.get("entity_id"),
            payload=dict(d.get("payload") or {}),
            origin=d.get("origin", "engine"),
            seq=int(d.get("seq", 0)),
            id=d.get("id", ""),
            key=d.get("key"),
            cause=d.get("cause"),
            ephemeral=bool(d.get("ephemeral", False)),
        )


def make_event(
    type_: EventType,
    tick: int,
    entity_id: str | None = None,
    payload: dict[str, Any] | None = None,
    origin: str = "engine",
    key: str | None = None,
    cause: str | None = None,
) -> Event:
    return Event(
        type=type_,
        tick=tick,
        entity_id=entity_id,
        payload=payload or {},
        origin=origin,
        key=key,
        cause=cause,
        ephemeral=type_ in EPHEMERAL_TYPES,
    )
