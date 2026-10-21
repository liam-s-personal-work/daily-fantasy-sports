"""Rebuild data/learned/calibration.json and blend.json from a graded forecaster experiment.

1. Calibration: per position, stretch factors for each half of the forecaster's range so that,
   on players who played, about 10% of outcomes fall below the stretched low and 10% above the
   stretched high (grid search; positions with fewer than 30 rows use the pooled value).
2. Blend weights: per position, the encompassing-regression weight of the calibrated forecast
   mean against the baseline mean, on every graded row (zeros included: both means carry the
   chance of not playing), shrunk toward the user's prior (dfs.grade.update_blend).

Both files record the weeks they were fit on; runs of those weeks ignore them.

    python scripts/fit_learned.py docs/results/forecast_experiment_2026_wk3-6_blind.csv
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from dfs.blend import LEARNED_DIR, PRIOR_FORECAST_WEIGHT, calibrate, forecast_to_projection
from dfs.grade import update_blend
from dfs.models import Forecast

GRID = np.round(np.arange(0.5, 2.51, 0.01), 2)


def _scale(gap: np.ndarray, width: np.ndarray) -> float:
    """Smallest stretch s with share(gap > s * width) <= 10% (gap: how far past 'likely' the outcome fell)."""
    for s in GRID:
        if np.mean(gap > s * width) <= 0.10:
            return float(s)
    return float(GRID[-1])


def fit_calibration(d: pd.DataFrame) -> dict:
    played = d[d["played"].astype(bool)]
    out = {}
    for pos, g in [("default", played)] + list(played.groupby("pos")):
        if len(g) < 30:
            continue
        lo_w = np.maximum(g.f_likely - g.f_low, 0.25)
        hi_w = np.maximum(g.f_high - g.f_likely, 0.25)
        out[pos] = {"low_scale": _scale((g.f_likely - g.actual).to_numpy(), lo_w.to_numpy()),
                    "high_scale": _scale((g.actual - g.f_likely).to_numpy(), hi_w.to_numpy()), "n": int(len(g))}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--out", default=str(LEARNED_DIR))
    a = ap.parse_args()
    d = pd.read_csv(a.csv).dropna(subset=["f_mean"])
    season = int(d["season"].iloc[0])
    weeks = [[season, int(w)] for w in sorted(d["week"].unique())]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    cal = {"nfl": fit_calibration(d), "_fit_weeks": weeks, "_source": f"fit on {a.csv} (players who played)"}
    (out / "calibration.json").write_text(json.dumps(cal, indent=1))

    errs = []
    for r in d.itertuples():
        f = Forecast(player_key=r.key, low=r.f_low, likely=r.f_likely, high=r.f_high, p_dnp=r.f_pdnp)
        fm = forecast_to_projection(calibrate(f, cal, "nfl", r.pos)).mean
        errs.append({"position": r.pos, "baseline_err": r.actual - r.b_mean, "forecast_err": r.actual - fm})
    p = out / "blend.json"
    p.unlink(missing_ok=True)
    st = update_blend(p, "nfl", errs, prior_weight=PRIOR_FORECAST_WEIGHT["nfl"])
    st["_fit_weeks"] = weeks
    st["_source"] = (f"encompassing regression of the calibrated forecast mean on {a.csv}, "
                     "shrunk toward the 0.85 prior (n0 = 200 pseudo-games)")
    p.write_text(json.dumps(st, indent=1))

    print("calibration:", json.dumps(cal["nfl"]))
    print("blend weights:", {k: (v["n"], v["weight"]) for k, v in st["nfl"].items()})
    # evaluation on the same rows (in-sample, for the record only)
    b = d.b_mean.to_numpy()
    fcal = np.array([forecast_to_projection(calibrate(Forecast(player_key=r.key, low=r.f_low, likely=r.f_likely,
                                                               high=r.f_high, p_dnp=r.f_pdnp), cal, "nfl", r.pos)).mean
                     for r in d.itertuples()])
    w = np.array([st["nfl"].get(pz, {}).get("weight", PRIOR_FORECAST_WEIGHT["nfl"]) for pz in d.pos])
    y = d.actual.to_numpy()
    for name, pred in (("baseline", b), ("forecast (calibrated)", fcal), ("learned blend", w * fcal + (1 - w) * b),
                       ("0.85 prior blend", 0.85 * fcal + 0.15 * b)):
        print(f"{name:>22}: MAE {np.mean(np.abs(y - pred)):.3f}  RMSE {np.sqrt(np.mean((y - pred) ** 2)):.3f}  "
              f"bias {np.mean(pred - y):+.3f}")


if __name__ == "__main__":
    sys.exit(main())
