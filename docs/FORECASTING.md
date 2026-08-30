# Forecasting

`nexus/forecasting` turns the twin's history into anticipation: a `HistoryRecorder` samples the world once per simulated
minute, a Holt-Winters model blended with the demand-profile prior forecasts arrivals and projected utilization, a
per-robot battery model predicts exhaustion and charger ETAs, a peak-occupancy estimator predicts zone congestion, and a
rule set turns all of that into ranked bottlenecks with concrete recommendations. Everything is deterministic and fast
(≈3.5 ms for a full forecast on the small scale, ≈8.8 ms on large), so the live loop refreshes a quick forecast every
simulated minute and a full one every five.

## History recorder (`history.py`)

`HistoryRecorder(sample_every_ticks=60, max_samples=2880, zone_window=60, battery_window=30)` is an engine hook
(`engine.hooks.append(recorder.hook)`). Each sample stores `tick, orders_created, orders_delta, delivered, open, pending,
breach_projected, congestion, utilization, mean_battery, robots_operational`; separate short windows keep per-zone
occupancy (congestion trend) and per-robot battery (drain rate). The projected breach uses the KPI definition exactly but
is computed from running stats and open orders only (O(open) instead of O(all orders)). The recorder is picklable and
`fork()`-able so simulation worlds carry a copy; `timeline_points()` feeds `GET /api/timeline`.

## Exponential smoothing (`smoothing.py`)

Notation: `x_t` observation, `L_t` level, `b_t` trend, `s_t` seasonal, `m` season length, `h` horizon step, `φ` damping.

* SES: `L_t = α·x_t + (1−α)·L_{t−1}`; forecast `x̂_{T+h} = L_T`.
* Holt (damped): `L_t = α·x_t + (1−α)(L_{t−1} + φ·b_{t−1})`, `b_t = β(L_t − L_{t−1}) + (1−β)·φ·b_{t−1}`;
  `x̂_{T+h} = L_T + (φ + φ² + … + φ^h)·b_T`.
* Additive Holt-Winters: `L_t = α(x_t − s_{t−m}) + (1−α)(L_{t−1} + φ·b_{t−1})`, `b_t` as above,
  `s_t = γ(x_t − L_t) + (1−γ)·s_{t−m}`; `x̂_{T+h} = L_T + (Σ_{i≤h} φ^i)·b_T + s_{T−m+((h−1) mod m)+1}`.
  Initialisation: `L_0` = mean of the first season, `b_0` = (mean of season 2 − mean of season 1)/m, `s_i` = average
  deviation of position `i` from its season mean.

`holt_winters_fit` degrades gracefully — Holt-Winters with ≥ 2 full seasons, Holt's linear trend with ≥ 4 points, SES
otherwise — and reports which model ran. `prediction_interval(residuals, z)` gives `z · RMSE`.

## Demand (`demand.py`)

Per simulated minute `m` of the horizon:

```
prior(m)    = demand.rate_per_tick(hour(t₀+m), t₀+m) · ticks_per_minute      (profile, bursts, multiplier)
learned(m)  = Holt-Winters / Holt forecast of the observed per-minute arrival series (season = 60 min, φ = 0.9)
forecast(m) = (1 − w)·prior(m) + w·learned(m),      w = 0.5 · min(1, n_samples / 120)
```

With no history the forecast *is* the profile; after two hours half of the weight sits on what actually happened.
Buckets (15 min) carry an ≈80 % prediction interval combining Poisson noise with the model's residual error:
`half_width = 1.28 · sqrt(expected + (w · σ_resid · bucket_minutes)²)`. Capacity is derived from the twin's own
throughput — `capacity/h = effective_robots · 3600 / mean_task_seconds · orders_per_task`, with
`effective_robots = operational − ½·(charging or heading to charge)` — giving `projected_utilization`, a
`rising/flat/falling` trend (±10 % vs the last 15 minutes) and a confidence that grows with history and shrinks with the
horizon.

## Battery (`battery.py`)

For each operational robot:

```
drain/min      = observed negative battery deltas over the history window
                 (fallback: util · drain_move · speed · 60/ts + drain_idle · 60/ts)
workload_drain = remaining_cells · drain_move + remaining_picks · pick_ticks · drain_action
task_minutes   = (remaining_cells / speed + picks · pick_ticks + unload_ticks) · ts / 60
exhaustion_min = task_minutes · battery / workload_drain                   if battery ≤ workload_drain
               = task_minutes + (battery − workload_drain) / drain_per_min   otherwise
```

`remaining_cells` uses the same nearest-neighbour × detour estimate as task planning; the charger ETA is the distance to
the nearest enabled charger (exact BFS distance for the riskiest robots when a `Pathfinder` is supplied). Risk is `high`
if exhaustion < ETA + 10 min (or the robot is below the engine's low threshold and not charging), `medium` if < 25 min,
else `low`. Recommendations read like "Send R04 to charging after current task (predicted exhaustion in 23 min, CH02
4 min away)".

## Congestion (`congestion.py`)

Mean occupancy (Little's law, `L = λ·W`) is small for every zone; congestion is a *clustering* phenomenon — robots converge
because the dispatcher hands out the oldest orders to every idle robot in the same tick. The forecast therefore estimates
the **peak** concurrent occupancy over the next minutes:

```
projected = 0.5·inside + 0.9·en_route + 0.85·wave + stream + trend
  inside   robots in the zone now (about half leave within one dwell time)
  en_route robots whose remaining path or next pick waypoint lies in the zone
  wave     orders among the next |idle robots| pending orders (FIFO) that require the zone
  stream   Little's-law mean concurrency of later dispatches: visits · dwell / horizon
  trend    recent occupancy slope from history × min(15, horizon) minutes (≥ 0)
```

`eta_min` is the median arrival time of converging robots (0 if already congested, the horizon if no crossing is
expected); risk is `high` if projected > capacity, `medium` if ≥ 0.75·capacity. Drivers name the open orders, robots en
route, rising occupancy, congested adjacent corridors and spill-over from closed zones.

## Bottlenecks (`bottleneck.py`)

| Kind | Rule | Severity |
|---|---|---|
| zone | medium/high congestion forecast | `projected / (2·capacity)` |
| dock | closed dock · queue ≥ 3 · no loader assigned | 0.5 · `queue/6` · 0.3 |
| worker | delayed/absent loader at a dock | 0.4 (0.6 if the dock is queued) |
| charger | robots needing charge > free slots · disabled stations | `0.3 + 0.7·(need − free)/need` · `0.2 + 0.2·disabled` |
| robot | one per failed robot | `0.3 + 3/fleet` |
| inventory | top-10 % SKU below the replenishment threshold | `0.4 + 0.6·(1 − rank/hot)` |
| demand | projected utilization > 0.9 · backlog older than half its SLA | `(util − 0.7)/0.5` · `age/SLA` |

Each bottleneck carries a recommendation the UI and the planner reuse ("Pre-position hot inventory from Zone C in
Zone B and stagger dispatch", "+2 robots or enable batching (3 orders/trip) to lift capacity to ~636 orders/h",
"Reassign R07's zone coverage to R01, R02 and simulate the reallocation before executing").

## The Forecaster (`forecaster.py`)

`Forecaster(horizon_min=90, congestion_horizon_min=30, bucket_min=15).forecast(world, history, pathfinder)` composes the
four parts into a `Forecast` (schema in `nexus/api/schemas.py`) with a deterministic 2–4 sentence summary, e.g.
*"Demand rising to ~419 orders/h (+23%) over the next 90 min; projected utilization 1.32 — above capacity. Zone F
congestion +325% expected in ~0 min: 4.2 robots vs capacity 3 (5 open orders require Zone F). R04 predicted to exhaust in
23 min; charger 4 min away."* `quick()` skips exact path distances and the SKU scan for the once-a-minute live loop.
The Operations Manager runs a forecast at the start of every decision and the planner prompt includes its summary.
