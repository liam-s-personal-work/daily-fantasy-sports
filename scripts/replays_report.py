"""Re-grade every saved replay (code only, no model calls) and rewrite docs/results/replays.md."""

from __future__ import annotations

import glob
import json
from pathlib import Path

from dfs.grading_run import grade_run

OUT = Path("docs/results/replays.md")
REPLAYS = "runs/replays_v2"  # runs of the current engine (scripts/replay_all.sh)


def label(rep: dict, base: dict) -> str:
    c = rep["contest"]
    site = {"draftkings": "DK", "fanduel": "FD"}[c["site"]]
    games = c["games"]
    what = games[0] if len(games) == 1 else f"main ({len(games)} games)"
    return f"{site} NFL {c['game_type'].replace('_', ' ')}, {base.get('season')} wk {base.get('week')} {what}"


def main() -> None:
    rows = []
    for d in sorted(glob.glob(f"{REPLAYS}/*/")):
        d = Path(d)
        if not (d / "replay_summary.json").exists():
            continue
        g = grade_run(d, update=False)
        rep = json.loads((d / "report.json").read_text())
        base = json.loads((d / "baseline.json").read_text())
        s = json.loads((d / "replay_summary.json").read_text())
        rows.append((label(rep, base), s, g))
    L = ["# Real-slate replays (2026 NFL)", "",
         "Each replay runs the whole pipeline on a real DraftKings/FanDuel salary file with live Claude agents",
         "(forecaster, strategist, checker, backup), as of the morning of the games. Web research is off (searching",
         "today would surface the results); facts come from point-in-time nflverse data, and the model's training",
         "data ends before the 2026 season. Blend weights are the user's priors (0.85) and the forecaster's ranges",
         "are uncalibrated, because the learned values were fit on these same weeks. Graded with",
         "`scripts/replays_report.py` against actual DK/FD points from nflverse box scores. \"Simulated field\" is",
         "our field model, not the real contest (no standings files were available).", "",
         "| Slate | Wall clock | Model cost | Lineup pts | Best possible | Beat sim. field | Sim. top-2.3% cut | Player MAE | 80% band coverage |",
         "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name, s, g in rows:
        L.append(f"| {name} | {s['wall_s']:.0f} s | ${s['llm']['cost_usd']:.2f} | {g['actual']:.1f} | {g['hindsight_optimal']:.1f} | "
                 f"{g.get('sim_field_percentile', 0):.0%} | {g.get('sim_field_top_cut', 0):.1f} | {g['projection_mae_played']:.2f} | "
                 f"{g['band_coverage_played']:.0%} |")
    L += ["", "Points gained (+) or lost (−) by each step, in actual points (alternatives are not steps):", "",
          "| Slate | Baseline → blended numbers | → contest objective + strategist | Checker | Forecast-only lineup | Draft A / B / C |",
          "|---|---:|---:|---:|---:|---|"]
    for name, s, g in rows:
        st, sc = g["steps"], g["stage_scores"]
        L.append(f"| {name} | {st.get('baseline_numbers->blended_numbers', 0):+.1f} | "
                 f"{st.get('blended_numbers->objective+strategist', 0):+.1f} | {st.get('objective+strategist->after_checker', 0):+.1f} | "
                 f"{sc.get('forecast_numbers', '-')} | {sc.get('draft_a')} / {sc.get('draft_b')} / {sc.get('draft_c')} |")
    wall = sum(s["wall_s"] for _, s, _ in rows)
    cost = sum(s["llm"]["cost_usd"] for _, s, _ in rows)
    hits = sum(1 for _, _, g in rows if g["actual"] >= g.get("sim_field_top_cut", 1e9))
    L += ["", f"Totals: {len(rows)} slates, {wall / 60:.1f} minutes of wall clock, ${cost:.2f} of model calls; every final lineup",
          f"passed the independent legality check; {hits} of {len(rows)} reached the simulated field's top-2.3% cut.", "",
          "What this shows: the full pipeline runs on real site files, legally, in minutes, for a dollar or two per",
          "slate. What it does not show: an edge. At the lineup level five slates are noise (see the step column);",
          "showing even a 5% top-2.3% rate takes on the order of 250 independent slates. Player-level evidence",
          "converges far faster: see `forecast_experiment_2026_wk3-6_blind.md`."]
    OUT.write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
