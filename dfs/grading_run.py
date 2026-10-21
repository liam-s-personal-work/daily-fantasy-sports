"""Grade a finished run directory against what actually happened, then learn from it.

1. Actual fantasy points for every contest player (NFL: nflverse box scores scored with the
   contest's own table; any sport: the site's contest-standings export, which carries FPTS).
2. The submitted lineup's score, the hindsight-best legal lineup, and each stage's lineup
   (baseline only, forecast only, the drafts, after the strategist, after the checker, final),
   so the step that cost the most points is named.
3. Finish percentile: against the real field when a standings file is given, otherwise against
   the simulated field (a model, labelled as such).
4. Learning: per-position forecast-vs-baseline errors update the blend weights (shrunk), and
   forecaster interval misses update the conformal widening offsets.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from dfs import strategy
from dfs.grade import Standings, grade_lineup, read_standings, update_blend
from dfs.io.contest_file import norm_name
from dfs.lastcall import load_run
from dfs.models import Contest, Lineup, Projection
from dfs.optimize.ilp import Infeasible, SolveOptions, lineup_points, solve

LEARNED = Path("data/learned")


def nfl_actuals(contest: Contest, ids: dict[str, str], season: int, week: int) -> dict[str, float]:
    from dfs.backtest import add_actual_points
    from dfs.projections.nfl_provider import load_data

    data = load_data(season, max_age_hours=1.0)
    table = contest.rules.scoring
    add_actual_points(data, [table])
    pg = data.player_games
    wk = pg[(pg["season"] == season) & (pg["week"] == week)]
    by_gsis = dict(zip(wk["gsis_id"], wk[f"fp_{table}"]))
    tg = data.team_games
    tw = tg[(tg["season"] == season) & (tg["week"] == week)]
    by_team = dict(zip(tw["team"], tw.get(f"dst_{table}", np.nan)))
    out = {}
    for k, i in ids.items():
        v = by_team.get(i.split(":", 1)[1]) if i.startswith("DST:") else by_gsis.get(i)
        out[k] = float(v) if v is not None and not np.isnan(v) else 0.0
    for k in contest.players:  # rostered but no stat row: did not play -> 0
        out.setdefault(k, 0.0)
    return out


def standings_actuals(contest: Contest, st: Standings) -> dict[str, float]:
    return {k: st.actual_fpts.get(norm_name(p.name), 0.0) for k, p in contest.players.items()}


def _lineup_on(contest: Contest, proj: dict[str, Projection], exclude: set[str], label: str) -> Lineup | None:
    try:
        return solve(contest, {k: p.mean for k, p in proj.items() if k not in exclude},
                     SolveOptions(time_limit_s=10), label=label)
    except Infeasible:
        return None


def simulated_field_percentile(contest: Contest, proj: dict[str, Projection], actual: dict[str, float],
                               points: float, seed: int, target_top: float) -> dict[str, float]:
    fm = strategy.field_for(contest, proj, kind=contest.kind, seed=seed)
    if fm is None:
        return {}
    scores = np.array([lineup_points(lu, actual) for lu in fm.lineups])
    w = np.asarray(fm.weights, dtype=float)
    beat = float(w[scores < points].sum() + 0.5 * w[scores == points].sum())
    order = np.argsort(-scores)
    cut = scores[order][np.searchsorted(np.cumsum(w[order]), target_top)]
    return {"sim_field_percentile": round(beat, 4), "sim_field_top_cut": round(float(cut), 2),
            "sim_field_best": round(float(scores.max()), 2), "sim_field_median": round(float(np.median(scores)), 2)}


def grade_run(run_dir: Path, standings: str | None = None, update: bool = True,
              learned_dir: Path = LEARNED) -> dict[str, Any]:
    st = load_run(run_dir)
    c: Contest = st["contest"]
    base = json.loads((run_dir / "baseline.json").read_text())
    report = st["report"]
    cfg = st["config"]
    stnd = read_standings(standings) if standings else None
    if stnd is not None:
        actual = standings_actuals(c, stnd)
    elif c.rules.sport == "nfl" and base.get("season") and base.get("week"):
        actual = nfl_actuals(c, base.get("ids") or {}, int(base["season"]), int(base["week"]))
    else:
        raise ValueError("no actual points: pass the site's contest standings export (--standings)")

    proj = st["projections"]
    baseline = st["baseline"]
    out = set(base.get("ruled_out") or [])
    # The chain of what would have been submitted after each step (same objective-free mean solve
    # for the two number sources, so the numbers step compares like with like).
    chain: dict[str, Lineup] = {}
    alts: dict[str, Lineup] = {}
    b_only = _lineup_on(c, baseline, out, "baseline numbers")
    if b_only:
        chain["baseline_numbers"] = b_only
    blended = _lineup_on(c, proj, out, "blended numbers")
    if blended:
        chain["blended_numbers"] = blended
    stl = report.get("stage_lineups") or {}
    for name in ("after_strategist", "after_checker", "final"):
        if name in stl:
            chain["objective+strategist" if name == "after_strategist" else name] = Lineup.model_validate(stl[name])
    if st["forecasts"]:
        from dfs.blend import forecast_to_projection
        fproj = {**baseline, **{k: forecast_to_projection(f) for k, f in st["forecasts"].items()}}
        f_only = _lineup_on(c, fproj, out, "forecast_only")
        if f_only:
            alts["forecast_numbers"] = f_only
    for d in report["decision"].get("drafts", []):
        lu = Lineup.model_validate(d)
        alts[lu.label.split(" - ")[0].lower().replace(" ", "_")] = lu
    final = st["final"]
    if (run_dir / "lastcall.json").exists():
        final = Lineup.model_validate(json.loads((run_dir / "lastcall.json").read_text())["final"])
        chain["after_lastcall"] = final
    stages = {**chain, **alts}

    g = grade_lineup(c, final, actual, projections=proj, stage_lineups=chain, alternatives=alts, standings=stnd,
                     target_top=float(cfg.get("target_top", 0.023)))
    res = g.to_dict()
    res["stage_scores"] = {n: round(lineup_points(lu, actual), 2) for n, lu in stages.items()}
    if stnd is None:
        res.update(simulated_field_percentile(c, proj, actual, g.actual, int(cfg.get("seed", 11)),
                                              float(cfg.get("target_top", 0.023))))
        res["percentile_note"] = "against the simulated field (a model), not the real contest"

    # projection accuracy on players who played
    rows = []
    for k, pr in proj.items():
        if k in actual and k in c.players:
            rows.append({"key": k, "pos": c.players[k].positions[0], "proj": pr.mean, "p10": pr.p10, "p90": pr.p90,
                         "actual": actual[k], "baseline": baseline[k].mean if k in baseline else None,
                         "forecast": st["forecasts"][k].likely if k in st["forecasts"] else None})
    played = [r for r in rows if r["actual"] != 0.0]
    res["projection_mae_played"] = round(float(np.mean([abs(r["actual"] - r["proj"]) for r in played])), 3) if played else None
    res["band_coverage_played"] = round(float(np.mean([r["p10"] <= r["actual"] <= r["p90"] for r in played])), 3) if played else None

    if update and st["forecasts"]:
        errs = [{"position": r["pos"], "baseline_err": r["actual"] - r["baseline"],
                 "forecast_err": r["actual"] - st["forecasts"][r["key"]].likely}
                for r in played if r["baseline"] is not None and r["forecast"] is not None]
        if errs:
            from dfs.blend import PRIOR_FORECAST_WEIGHT
            update_blend(learned_dir / "blend.json", c.rules.sport, errs,
                         prior_weight=PRIOR_FORECAST_WEIGHT.get(c.rules.sport, 0.0))
            res["blend_updated_with"] = len(errs)
    (run_dir / "grade.json").write_text(json.dumps(res, indent=1, default=lambda o: o.model_dump() if hasattr(o, "model_dump") else str(o)))
    return res
