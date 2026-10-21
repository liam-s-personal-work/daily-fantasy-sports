"""Replay a past slate end to end with live agents, then grade it against what happened.

Web research is off by default in replays: searching today would surface the games' results
(look-ahead leakage). The forecaster sees only point-in-time structured facts, and the model's
training data ends before the 2026 season, so it cannot remember these outcomes.

    python scripts/replay.py data/cache/real_samples/dk_classic_wk6_main_18Oct2026.csv \
        --season 2026 --week 6 --now 2026-10-18T16:00:00+00:00
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

from dfs.grading_run import grade_run
from dfs.pipeline import RunConfig, run


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--week", type=int, required=True)
    ap.add_argument("--now", required=True, help="ISO time the run pretends it is (before kickoff)")
    ap.add_argument("--out", default="runs/replays")
    ap.add_argument("--no-forecast", action="store_true")
    ap.add_argument("--web", action="store_true", help="allow web research (leaks results for past games)")
    ap.add_argument("--budget", type=float, default=15.0)
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--kind", choices=["gpp", "cash"], default=None)
    ap.add_argument("--weight", type=float, default=None, help="override the forecast blend weight")
    a = ap.parse_args()
    cfg = RunConfig(out_dir=Path(a.out), season=a.season, week=a.week, web_research=a.web,
                    forecaster=not a.no_forecast, budget_usd=a.budget, concurrency=a.concurrency,
                    learned_blend=None, forecast_weight_override=a.weight)
    t0 = time.monotonic()
    res = run(a.file, cfg, now=datetime.fromisoformat(a.now))
    wall = time.monotonic() - t0
    g = grade_run(res.run_dir, update=False)
    keep = {k: g.get(k) for k in ("actual", "projected", "hindsight_optimal", "efficiency", "failed_step", "stage_scores",
                                  "sim_field_percentile", "sim_field_top_cut", "sim_field_median",
                                  "projection_mae_played", "band_coverage_played", "notes")}
    out = {"run_dir": str(res.run_dir), "wall_s": round(wall, 1), "llm": res.report["llm"],
           "timing_s": res.report["timing_s"], "grade": keep}
    (res.run_dir / "replay_summary.json").write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
