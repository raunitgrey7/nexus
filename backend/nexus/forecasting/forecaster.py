"""The Forecaster composes demand, battery, congestion and bottleneck forecasts into one
:class:`~nexus.api.schemas.Forecast` with a deterministic natural-language summary."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nexus.api.schemas import BatteryForecast, Bottleneck, CongestionForecast, DemandForecast, Forecast
from nexus.forecasting.battery import forecast_battery
from nexus.forecasting.bottleneck import detect_bottlenecks
from nexus.forecasting.congestion import forecast_congestion
from nexus.forecasting.demand import forecast_demand
from nexus.forecasting.history import HistoryRecorder
from nexus.twin.world import WorldState

if TYPE_CHECKING:
    from nexus.simulation.pathfinding import Pathfinder


class Forecaster:
    def __init__(self, horizon_min: int = 90, congestion_horizon_min: int = 30, bucket_min: int = 15) -> None:
        self.horizon_min = max(5, int(horizon_min))
        self.congestion_horizon_min = max(5, int(congestion_horizon_min))
        self.bucket_min = max(1, int(bucket_min))

    def forecast(
        self,
        world: WorldState,
        history: HistoryRecorder | None = None,
        pathfinder: Pathfinder | None = None,
        horizon_min: int | None = None,
    ) -> Forecast:
        horizon = int(horizon_min) if horizon_min else self.horizon_min
        demand = forecast_demand(world, history, horizon_min=horizon, bucket_min=self.bucket_min)
        battery = forecast_battery(world, pathfinder=pathfinder, history=history)
        congestion = forecast_congestion(
            world, history=history, horizon_min=min(horizon, self.congestion_horizon_min)
        )
        bottlenecks = detect_bottlenecks(world, demand, battery, congestion, deep=True)
        return Forecast(
            generated_tick=world.clock.tick,
            sim_time=world.clock.now().isoformat(),
            demand=demand,
            battery=battery,
            congestion=congestion,
            bottlenecks=bottlenecks,
            summary=self.summary(world, demand, battery, congestion, bottlenecks),
        )

    def quick(self, world: WorldState, history: HistoryRecorder | None = None) -> Forecast:
        """Cheap subset for the once-a-minute live loop: no exact path distances, no SKU scan."""
        demand = forecast_demand(world, history, horizon_min=self.horizon_min, bucket_min=self.bucket_min)
        battery = forecast_battery(world, pathfinder=None, history=history)
        congestion = forecast_congestion(world, history=history, horizon_min=self.congestion_horizon_min)
        bottlenecks = detect_bottlenecks(world, demand, battery, congestion, deep=False)
        return Forecast(
            generated_tick=world.clock.tick,
            sim_time=world.clock.now().isoformat(),
            demand=demand,
            battery=battery,
            congestion=congestion,
            bottlenecks=bottlenecks,
            summary=self.summary(world, demand, battery, congestion, bottlenecks),
        )

    @staticmethod
    def summary(
        world: WorldState,
        demand: DemandForecast,
        battery: list[BatteryForecast],
        congestion: list[CongestionForecast],
        bottlenecks: list[Bottleneck],
    ) -> str:
        sentences: list[str] = []
        cur = demand.current_rate_per_hour
        fc = demand.forecast_rate_per_hour
        change = (fc - cur) / cur * 100.0 if cur > 1e-9 else 0.0
        verb = {"rising": "rising to", "falling": "easing to", "flat": "steady at"}[demand.trend]
        change_txt = f" ({change:+.0f}%)" if cur > 1e-9 and abs(change) >= 1 else ""
        util = demand.projected_utilization
        util_txt = f"projected utilization {util:.2f}" + (" — above capacity" if util > 1.0 else "")
        sentences.append(
            f"Demand {verb} ~{fc:.0f} orders/h{change_txt} over the next {demand.horizon_min} min; {util_txt}."
        )

        hot = [c for c in congestion if c.risk != "low"]
        if hot:
            c = hot[0]
            when = "now" if c.eta_min <= 0 else f"in ~{c.eta_min:.0f} min"
            driver = f" ({c.drivers[0]})" if c.drivers and c.drivers[0] != "no inbound demand" else ""
            sentences.append(
                f"{c.zone_name} congestion {c.projected_change_pct:+.0f}% expected {when}: "
                f"{c.projected_robots:.1f} robots vs capacity {c.capacity}{driver}."
            )

        risky = [b for b in battery if b.risk != "low" and b.predicted_exhaustion_min is not None]
        if risky:
            b = risky[0]
            eta = f"; charger {b.charger_eta_min:.0f} min away" if b.charger_eta_min is not None else ""
            sentences.append(
                f"{b.robot_id} predicted to exhaust in {b.predicted_exhaustion_min:.0f} min{eta}."
            )

        covered = {("zone", c.zone_id) for c in hot[:1]} | {("robot", b.robot_id) for b in risky[:1]}
        for bn in bottlenecks:
            if (bn.kind, bn.entity_id) in covered:
                continue
            sentences.append(f"{bn.message}. Recommended: {bn.recommendation}.")
            break
        if len(sentences) == 1:
            sentences.append(
                f"No congestion or battery risks detected across {len(world.robots)} robots and {len(congestion)} zones."
            )
        return " ".join(sentences[:4])
