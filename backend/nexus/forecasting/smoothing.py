"""Exponential smoothing family used by the demand forecaster.

All functions are pure, deterministic and dependency-free. ``*_fit`` variants also return the
one-step-ahead fitted values so callers can estimate residual variance for prediction intervals.

Notation: ``x_t`` observation, ``L_t`` level, ``b_t`` trend, ``s_t`` seasonal component, ``m`` season
length, ``h`` horizon step, ``φ`` trend damping (``φ = 1`` is the classic undamped form).

* Simple exponential smoothing (SES): ``L_t = α·x_t + (1−α)·L_{t−1}``; forecast ``x̂_{T+h} = L_T``.
* Holt's linear trend (damped): ``L_t = α·x_t + (1−α)(L_{t−1} + φ·b_{t−1})``,
  ``b_t = β(L_t − L_{t−1}) + (1−β)·φ·b_{t−1}``; forecast ``x̂_{T+h} = L_T + (φ + φ² + … + φ^h)·b_T``.
* Additive Holt–Winters: ``L_t = α(x_t − s_{t−m}) + (1−α)(L_{t−1} + φ·b_{t−1})``,
  ``b_t`` as above, ``s_t = γ(x_t − L_t) + (1−γ)·s_{t−m}``;
  forecast ``x̂_{T+h} = L_T + (Σ_{i≤h} φ^i)·b_T + s_{T−m+((h−1) mod m)+1}``.
  Initialisation: ``L_0`` = mean of the first season, ``b_0`` = (mean of season 2 − mean of season 1)/m,
  ``s_i`` = average deviation of position ``i`` from its season mean over the available full seasons.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def _zeros(horizon: int) -> list[float]:
    return [0.0] * max(0, horizon)


def ses_fit(series: Sequence[float], alpha: float = 0.3, horizon: int = 1) -> tuple[list[float], list[float]]:
    if not series:
        return _zeros(horizon), []
    level = float(series[0])
    fitted: list[float] = []
    for x in series:
        fitted.append(level)
        level = alpha * float(x) + (1.0 - alpha) * level
    return [level] * max(0, horizon), fitted


def ses(series: Sequence[float], alpha: float = 0.3, horizon: int = 1) -> list[float]:
    """Simple exponential smoothing forecast (flat)."""
    return ses_fit(series, alpha, horizon)[0]


def linear_trend_fit(
    series: Sequence[float], alpha: float = 0.3, beta: float = 0.1, horizon: int = 1, phi: float = 1.0
) -> tuple[list[float], list[float]]:
    n = len(series)
    if n == 0:
        return _zeros(horizon), []
    if n == 1:
        return [float(series[0])] * max(0, horizon), [float(series[0])]
    level = float(series[0])
    trend = float(series[1]) - float(series[0])
    fitted: list[float] = []
    for x in series:
        pred = level + phi * trend
        fitted.append(pred)
        prev = level
        level = alpha * float(x) + (1.0 - alpha) * pred
        trend = beta * (level - prev) + (1.0 - beta) * phi * trend
    out: list[float] = []
    damp = 0.0
    for h in range(1, max(0, horizon) + 1):
        damp += phi**h
        out.append(level + damp * trend)
    return out, fitted


def linear_trend(
    series: Sequence[float], alpha: float = 0.3, beta: float = 0.1, horizon: int = 1, phi: float = 1.0
) -> list[float]:
    """Holt's linear trend forecast (optionally damped with ``phi`` < 1)."""
    return linear_trend_fit(series, alpha, beta, horizon, phi)[0]


def holt_winters_fit(
    series: Sequence[float],
    season_length: int,
    alpha: float = 0.3,
    beta: float = 0.05,
    gamma: float = 0.2,
    horizon: int = 1,
    phi: float = 1.0,
    nonnegative: bool = False,
) -> tuple[list[float], list[float], str]:
    """Additive Holt–Winters with graceful degradation.

    Returns ``(forecast, fitted, method)`` where ``method`` records which model actually ran:
    ``"holt-winters"`` (≥ 2 full seasons), ``"holt-linear"`` (≥ 4 points) or ``"ses"``.
    """
    m = int(season_length)
    n = len(series)
    if m < 2 or n < 2 * m:
        if n >= 4:
            fc, fallback_fitted = linear_trend_fit(series, alpha, max(beta, 0.05), horizon, phi)
            method = "holt-linear"
        else:
            fc, fallback_fitted = ses_fit(series, alpha, horizon)
            method = "ses"
        if nonnegative:
            fc = [max(0.0, v) for v in fc]
        return fc, fallback_fitted, method

    seasons = n // m
    means = [sum(float(v) for v in series[k * m : (k + 1) * m]) / m for k in range(seasons)]
    level = means[0]
    trend = (means[1] - means[0]) / m
    seasonal = [sum(float(series[k * m + i]) - means[k] for k in range(seasons)) / seasons for i in range(m)]
    fitted: list[float] = []
    for t, raw in enumerate(series):
        x = float(raw)
        s_prev = seasonal[t % m]
        pred = level + phi * trend + s_prev
        fitted.append(pred)
        prev = level
        level = alpha * (x - s_prev) + (1.0 - alpha) * (prev + phi * trend)
        trend = beta * (level - prev) + (1.0 - beta) * phi * trend
        seasonal[t % m] = gamma * (x - level) + (1.0 - gamma) * s_prev
    out: list[float] = []
    damp = 0.0
    for h in range(1, max(0, horizon) + 1):
        damp += phi**h
        value = level + damp * trend + seasonal[(n + h - 1) % m]
        out.append(max(0.0, value) if nonnegative else value)
    return out, fitted, "holt-winters"


def holt_winters(
    series: Sequence[float],
    season_length: int,
    alpha: float = 0.3,
    beta: float = 0.05,
    gamma: float = 0.2,
    horizon: int = 1,
    phi: float = 1.0,
    nonnegative: bool = False,
) -> list[float]:
    """Additive Holt–Winters forecast for ``horizon`` steps (see module docstring for formulas)."""
    return holt_winters_fit(series, season_length, alpha, beta, gamma, horizon, phi, nonnegative)[0]


def residuals(series: Sequence[float], fitted: Sequence[float], skip: int = 2) -> list[float]:
    """One-step-ahead residuals ``x_t − x̂_t``, skipping the warm-up points."""
    return [float(x) - float(f) for x, f in list(zip(series, fitted, strict=False))[skip:]]


def prediction_interval(resid: Sequence[float], z: float = 1.28) -> float:
    """Half-width of a symmetric prediction interval: ``z · RMSE`` (``z = 1.28`` ≈ 80 % two-sided)."""
    if not resid:
        return 0.0
    rmse = math.sqrt(sum(r * r for r in resid) / len(resid))
    return z * rmse


def linear_slope(ys: Sequence[float], xs: Sequence[float] | None = None) -> float:
    """Least-squares slope of ``ys`` against ``xs`` (defaults to 0, 1, 2, …)."""
    n = len(ys)
    if n < 2:
        return 0.0
    x_vals = [float(v) for v in xs] if xs is not None else [float(i) for i in range(n)]
    mean_x = sum(x_vals) / n
    mean_y = sum(float(v) for v in ys) / n
    var = sum((x - mean_x) ** 2 for x in x_vals)
    if var <= 0:
        return 0.0
    cov = sum((x - mean_x) * (float(y) - mean_y) for x, y in zip(x_vals, ys, strict=True))
    return cov / var


def mape(actual: Sequence[float], predicted: Sequence[float]) -> float:
    pairs = [(float(a), float(p)) for a, p in zip(actual, predicted, strict=True) if abs(float(a)) > 1e-9]
    if not pairs:
        return 0.0
    return sum(abs(a - p) / abs(a) for a, p in pairs) / len(pairs)
