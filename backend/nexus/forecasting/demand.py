"""Demand forecasting: learned arrivals blended with the twin's demand profile.

Per simulated minute ``m`` of the horizon::

    prior(m)    = demand.rate_per_tick(hour(t₀+m), t₀+m) · ticks_per_minute      (profile, bursts, multiplier)
    learned(m)  = Holt–Winters / Holt forecast of the observed per-minute arrival series
    forecast(m) = (1 − w)·prior(m) + w·learned(m),      w = 0.5 · min(1, n_samples / 120)

so with no history the forecast *is* the profile, and after two hours of observation half of the
weight sits on what actually happened. Bucket prediction intervals combine Poisson noise with the
model's one-step residual error::

    half_width = z · sqrt(expected + (w · σ_resid · bucket_minutes)²),   z = 1.28 (≈ 80 %)

Capacity is derived from the twin's own throughput::

    capacity/h = effective_robots · 3600 / (mean_task_seconds) · orders_per_task
    effective_robots = operational − ½ · (charging or heading to charge)
"""

from __future__ import annotations

import math

from nexus.api.schemas import DemandBucket, DemandForecast
from nexus.forecasting._common import (
    charging_robots,
    clamp,
    mean_orders_per_task,
    mean_task_ticks,
)
from nexus.forecasting.history import HistoryRecorder
from nexus.forecasting.smoothing import holt_winters_fit, prediction_interval, residuals
from nexus.twin.world import WorldState

Z_80 = 1.28
MAX_LEARNED_WEIGHT = 0.5
FULL_HISTORY_SAMPLES = 120


def profile_per_minute(world: WorldState, start_tick: int, horizon_min: int) -> list[float]:
    """Orders per minute implied by the demand profile for each minute of the horizon."""
    ts = world.clock.tick_seconds
    ticks_per_min = 60.0 / ts
    clk = world.clock.copy()
    out: list[float] = []
    for m in range(horizon_min):
        clk.tick = start_tick + round(m * ticks_per_min)
        out.append(world.demand.rate_per_tick(clk.hour_of_day(), clk.tick, ts) * ticks_per_min)
    return out


def capacity_per_hour(world: WorldState) -> tuple[float, float]:
    """(orders per hour the fleet can absorb, effective robot count)."""
    operational = len(world.operational_robots())
    effective = max(0.0, operational - 0.5 * len(charging_robots(world)))
    task_seconds = mean_task_ticks(world) * world.clock.tick_seconds
    capacity = effective * 3600.0 / task_seconds * mean_orders_per_task(world)
    return capacity, effective


def forecast_demand(
    world: WorldState,
    history: HistoryRecorder | None = None,
    horizon_min: int = 90,
    bucket_min: int = 15,
) -> DemandForecast:
    horizon = max(1, int(horizon_min))
    bucket = max(1, min(int(bucket_min), horizon))
    t0 = world.clock.tick
    prior = profile_per_minute(world, t0, horizon)

    learned: list[float] | None = None
    n_samples = 0
    sigma = 0.0
    method = "profile-prior"
    if history is not None and len(history) >= 2:
        per_min = history.per_minute_series()
        n_samples = len(per_min)
        if n_samples >= 10:
            mins_per_sample = history.minutes_per_sample()
            steps = max(1, math.ceil(horizon / mins_per_sample))
            season = max(2, round(60.0 / mins_per_sample))
            fc, fitted, used = holt_winters_fit(per_min, season, horizon=steps, phi=0.9, nonnegative=True)
            learned = [fc[min(steps - 1, int(m / mins_per_sample))] for m in range(horizon)]
            sigma = prediction_interval(residuals(per_min, fitted), z=1.0)  # 1σ per minute
            method = f"{used}+profile-prior"

    weight = MAX_LEARNED_WEIGHT * min(1.0, n_samples / FULL_HISTORY_SAMPLES) if learned is not None else 0.0
    per_minute = [
        (1.0 - weight) * p + weight * (learned[m] if learned is not None else p) for m, p in enumerate(prior)
    ]

    buckets: list[DemandBucket] = []
    for start in range(0, horizon, bucket):
        end = min(horizon, start + bucket)
        expected = sum(per_minute[start:end])
        half = Z_80 * math.sqrt(max(0.0, expected) + (weight * sigma * (end - start)) ** 2)
        buckets.append(
            DemandBucket(
                start_min=start,
                end_min=end,
                expected_orders=round(expected, 2),
                lower=round(max(0.0, expected - half), 2),
                upper=round(expected + half, 2),
            )
        )
    expected_total = sum(per_minute)
    forecast_rate = expected_total / horizon * 60.0
    if history is not None and len(history) >= 3:
        current_rate = history.arrival_rate_per_min(15) * 60.0
    else:
        current_rate = prior[0] * 60.0
    if current_rate <= 1e-9:
        trend = "rising" if forecast_rate > 1e-9 else "flat"
    elif forecast_rate > current_rate * 1.10:
        trend = "rising"
    elif forecast_rate < current_rate * 0.90:
        trend = "falling"
    else:
        trend = "flat"

    capacity, _effective = capacity_per_hour(world)
    if capacity > 1e-9:
        utilization = min(10.0, forecast_rate / capacity)
    else:
        utilization = 10.0 if forecast_rate > 1e-9 else 0.0
    confidence = clamp(
        0.45 + 0.5 * min(1.0, n_samples / FULL_HISTORY_SAMPLES) - 0.15 * min(1.0, horizon / 240.0), 0.2, 0.95
    )
    return DemandForecast(
        horizon_min=horizon,
        expected_orders=round(expected_total, 2),
        per_bucket=buckets,
        trend=trend,  # type: ignore[arg-type]
        current_rate_per_hour=round(current_rate, 2),
        forecast_rate_per_hour=round(forecast_rate, 2),
        capacity_per_hour=round(capacity, 2),
        projected_utilization=round(utilization, 4),
        confidence=round(confidence, 3),
        method=method,
    )
