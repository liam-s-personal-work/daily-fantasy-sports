"""Blend the statistical baseline with the blinded forecast, per player.

Quantile averaging (Vincentization): each of p10/p50/p90 is the weighted average of the two
sources' same quantile; p_zero is averaged too. The forecaster's intervals are widened first
by learned per-position offsets (conformal calibration), because LLM intervals run narrow.

Weights: the user's starting priors (NFL 0.85 forecast, college 1.0, other sports 0.0 =
baseline only), replaced per position by learned weights once a weights file has enough
graded player-games behind it (see dfs.grade.update_blend).
"""

from __future__ import annotations

import json
from pathlib import Path

from dfs.distributions import mixture_mean
from dfs.models import Forecast, Player, Projection

PRIOR_FORECAST_WEIGHT = {"nfl": 0.85, "cfb": 1.0}
MIN_LEARNED_N = 150


def forecast_weight(sport: str, position: str, learned: dict | None = None) -> float:
    rec = ((learned or {}).get(sport) or {}).get(position)
    if rec and rec.get("n", 0) >= MIN_LEARNED_N:
        return float(rec["weight"])
    return PRIOR_FORECAST_WEIGHT.get(sport, 0.0)


def load_learned(path: str | Path | None) -> dict:
    if path and Path(path).exists():
        return json.loads(Path(path).read_text())
    return {}


def calibrate(f: Forecast, widen: dict[str, float] | None, position: str) -> Forecast:
    """Conformal widening: move low down and high up by the learned per-position offset (points)."""
    q = (widen or {}).get(position, 0.0)
    if not q:
        return f
    return f.model_copy(update={"low": f.low - q, "high": f.high + q})


def forecast_to_projection(f: Forecast, floor: float | None = None) -> Projection:
    lo = f.low if floor is None else max(f.low, floor)
    return Projection(player_key=f.player_key, mean=mixture_mean(lo, f.likely, f.high, f.p_dnp),
                      p10=lo, p50=f.likely, p90=f.high, p_zero=f.p_dnp, source="forecast",
                      notes=f.rationale)


def blend(baseline: dict[str, Projection], forecast: dict[str, Forecast], players: dict[str, Player],
          sport: str, learned: dict | None = None, widen: dict[str, float] | None = None,
          weight_override: float | None = None) -> dict[str, Projection]:
    out: dict[str, Projection] = {}
    for k in set(baseline) | set(forecast):
        b = baseline.get(k)
        f = forecast.get(k)
        pos = players[k].positions[0] if k in players and players[k].positions else "?"
        if f is not None:
            f = calibrate(f, widen, pos)
        if f is None or b is None:
            src = b if b is not None else forecast_to_projection(f)
            out[k] = src.model_copy(update={"source": src.source if f is None else "forecast",
                                            "components": {**src.components, "w_forecast": 0.0 if f is None else 1.0}})
            continue
        w = weight_override if weight_override is not None else forecast_weight(sport, pos, learned)
        p10 = w * f.low + (1 - w) * b.p10
        p50 = w * f.likely + (1 - w) * b.p50
        p90 = w * f.high + (1 - w) * b.p90
        pz = w * f.p_dnp + (1 - w) * b.p_zero
        out[k] = Projection(player_key=k, mean=round(mixture_mean(p10, p50, p90, pz), 3), p10=p10, p50=p50, p90=p90,
                            p_zero=pz, source="blend",
                            components={"baseline_mean": b.mean, "forecast_likely": f.likely, "w_forecast": w},
                            notes=f.rationale)
    return out
