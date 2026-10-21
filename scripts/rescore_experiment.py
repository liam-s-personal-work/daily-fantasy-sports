"""Re-score a saved forecaster experiment against the current NFL baseline (no new model calls).

The forecasts in the experiment CSV are kept as they were; only the baseline columns are
recomputed with the current ``NFLParams`` defaults, the comparison is re-run, and (with
--update) the learned blend weights are rebuilt from the result.

    python scripts/rescore_experiment.py docs/results/forecast_experiment_2026_wk3-6_blind.csv --update
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from forecast_experiment import summarize, week_pool  # noqa: E402

from dfs.blend import LEARNED_DIR, PRIOR_FORECAST_WEIGHT
from dfs.grade import update_blend
from dfs.projections.nfl_provider import load_data


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--update", action="store_true", help="rebuild data/learned/blend.json from the result")
    a = ap.parse_args()
    df = pd.read_csv(a.csv)
    season = int(df["season"].iloc[0])
    data = load_data(season)
    parts = []
    for wk, g in df.groupby("week"):
        _, pool = week_pool(season, int(wk), data)
        b = pool.set_index("player_key")
        g = g[g["key"].isin(b.index)].copy()
        g["b_mean"] = g["key"].map(b["mean"]).astype(float)
        g["b_played"] = g["key"].map(b["played_mean"]).astype(float).fillna(g["b_mean"])
        g["b_p10"] = g["key"].map(b["p10"]).astype(float)
        g["b_p90"] = g["key"].map(b["p90"]).astype(float)
        parts.append(g)
        print(f"week {wk}: {len(g)} rows re-scored")
    out = pd.concat(parts)
    path = Path(a.csv)
    stem = path.stem + "_rescored"
    out.to_csv(path.with_name(stem + ".csv"), index=False)
    text = (f"## {path.stem}, re-scored against the current (tuned) NFL baseline\n\n"
            "Forecasts are unchanged from the original run; only the baseline columns were recomputed.\n"
            + summarize(out))
    path.with_name(stem + ".md").write_text(text + "\n")
    print(text)
    if a.update:
        d = out.dropna(subset=["f_mean"])
        errs = [{"position": r.pos, "baseline_err": r.actual - r.b_mean, "forecast_err": r.actual - r.f_mean}
                for r in d.itertuples()]
        p = LEARNED_DIR / "blend.json"
        p.unlink(missing_ok=True)
        st = update_blend(p, "nfl", errs, prior_weight=PRIOR_FORECAST_WEIGHT["nfl"])
        print({k: (v["n"], v["weight"]) for k, v in st["nfl"].items()})


if __name__ == "__main__":
    main()
