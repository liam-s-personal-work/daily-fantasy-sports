"""Turn a low / likely / high triple into a sampleable fantasy-point distribution.

low/likely/high are read as the 10th/50th/90th percentiles of the *played* outcome. The
quantile function is a two-piece normal joined at the median:

    Q(v) = p50 + s_low  * z(v)   for v < 0.5
    Q(v) = p50 + s_high * z(v)   for v >= 0.5,   s = (distance to p10 or p90) / z(0.9)

so all three inputs are matched exactly, each half holds half the mass, and fantasy
scoring's long right tail (touchdowns, big plays) is kept. An optional point mass at zero
models "doesn't play / leaves early". Closed forms keep simulation cheap.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.stats import norm

Z90 = float(norm.ppf(0.9))  # 1.2816
_INV_SQRT_2PI = 1.0 / math.sqrt(2.0 * math.pi)


def two_piece_params(p10: float, p50: float, p90: float) -> tuple[float, float, float]:
    """Return (median, s_low, s_high), with spreads floored so nothing is degenerate."""
    floor = max(0.05 * abs(p50), 0.25) / Z90
    s_low = max(max(p50 - p10, 0.0) / Z90, floor)
    s_high = max(max(p90 - p50, 0.0) / Z90, floor)
    return p50, s_low, s_high


def played_mean(p10: float, p50: float, p90: float) -> float:
    m, sl, sh = two_piece_params(p10, p50, p90)
    return m + (sh - sl) * _INV_SQRT_2PI


def played_sd(p10: float, p50: float, p90: float) -> float:
    _, sl, sh = two_piece_params(p10, p50, p90)
    var = 0.5 * (sl * sl + sh * sh) - ((sh - sl) * _INV_SQRT_2PI) ** 2
    return math.sqrt(max(var, 1e-9))


def mixture_mean(p10: float, p50: float, p90: float, p_zero: float) -> float:
    """Mean including the point mass at zero."""
    return (1.0 - p_zero) * played_mean(p10, p50, p90)


def quantiles_from_mean_sd(mean: float, sd: float, skew: float = 0.3) -> tuple[float, float, float]:
    """A right-skewed 10/50/90 triple whose two-piece mean and sd equal ``mean``/``sd``.

    ``skew`` in [0, 1): s_low = s(1-skew), s_high = s(1+skew).
    """
    sd = max(sd, 0.5)
    s = sd / math.sqrt(1.0 + skew * skew * (1.0 - 2.0 / math.pi))
    s_low, s_high = s * (1 - skew), s * (1 + skew)
    p50 = mean - (s_high - s_low) * _INV_SQRT_2PI
    return p50 - Z90 * s_low, p50, p50 + Z90 * s_high


def sample_from_uniform(u: np.ndarray, p10: float, p50: float, p90: float, p_zero: float = 0.0,
                        floor: float | None = None) -> np.ndarray:
    """Map uniforms in (0,1) to points.

    The lowest ``p_zero`` of the uniform range maps to the zero mass, so when uniforms come
    from a correlated copula, busts correlate too (a QB exit drags his receivers down).
    """
    m, sl, sh = two_piece_params(p10, p50, p90)
    u = np.clip(np.asarray(u, dtype=float), 1e-12, 1 - 1e-12)
    out = np.zeros_like(u)
    played = u >= p_zero
    v = (u[played] - p_zero) / (1.0 - p_zero) if p_zero > 0 else u[played]
    z = norm.ppf(np.clip(v, 1e-12, 1 - 1e-12))
    x = m + np.where(z < 0, sl, sh) * z
    if floor is not None:
        x = np.maximum(x, floor)
    out[played] = x
    return out
