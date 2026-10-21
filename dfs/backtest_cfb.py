"""Walk-forward backtest of the CFB baseline (``dfs.projections.cfb``) against real results.

Protocol (no target-week data reaches a projection: features use ``before(season, week)`` only):

- Fit: hyper-parameters are grid-searched on 2025 weeks 3-13 (2024 = prior season), then the
  dispersion (SD = a + b * mean, skew) and p_zero tables are fit on the same 2025 rows.
- Test: 2026 weeks 3-7 (2025 = prior season), every FBS-vs-FBS game, out of sample.
- Actuals: the week's stat lines (pbp, repaired from the official box where the pbp is
  incomplete) scored with ``dfs.scoring`` for the table; no line in the game = 0 points.

Pools:

- ``dfs`` (pre-game, primary): per team in an FBS-vs-FBS game, the top usage players over the
  team's last two games: QB1, RB1-2, WR/TE1-4 (raw recent share, ties by season share). Chosen
  before kickoff; a player who then does not play scores 0. Compares the model ``mean``
  (p_zero included) with naive season-to-date average and last-game points.
- ``role`` (post-hoc): players who recorded >= 5 touches+targets (QB: >= 10 dropbacks+carries)
  and were projected. Compares ``mean_played`` (the played-outcome mean); 10-90 coverage uses
  the played distribution. Role players the model did not project (no role in the last two
  games) are counted as misses.

Run: ``python -m dfs.backtest_cfb [--fit] [--table draftkings.cfb] [--out docs/results/cfb_baseline_backtest.md]``.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from dfs.distributions import quantiles_from_mean_sd, sample_from_uniform
from dfs.projections import cfb as m

REPO = Path(__file__).resolve().parents[1]
POOL_CAPS = {"QB": 1, "RB": 2, "WRTE": 4}


def fbs_team_ids(data: m.CFBData, season: int, week: int) -> set[int]:
    tg = data.team_games[(data.team_games["season"] == season) & (data.team_games["week"] == week)]
    if "fbs_game" in tg.columns:
        tg = tg[tg["fbs_game"].fillna(False).astype(bool)]
    return set(tg["team_id"].astype(int))


def week_rows(data: m.CFBData, season: int, week: int, table_id: str, params: m.CFBParams,
              dispersion=None, pzero=None, feats: pd.DataFrame | None = None,
              quantiles: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(projected rows joined with actuals, role players the model did not project) for one week."""
    teams = fbs_team_ids(data, season, week)
    if feats is None:
        feats = m.features(data, season, week, table_id, params, team_ids=teams)
    if feats.empty:
        return pd.DataFrame(), pd.DataFrame()
    slope = m.role_prior_slope(data, season, week, table_id, params.usage_basis)
    proj = m.project(feats, slope, params, dispersion, pzero) if quantiles else _project_fast(feats, slope, params, pzero)
    act = m.actuals(data, season, week, table_id)
    act = act[act["team_id"].isin(teams)]
    rows = proj.merge(act.drop(columns=["game_id"]), on=["athlete_id", "team_id"], how="left", suffixes=("", "_act"))
    rows["played"] = rows["fp"].notna()
    rows["actual"] = rows["fp"].fillna(0.0)
    for c in ("touches", "qb_plays"):
        rows[c] = rows[c].fillna(0.0)
    rows["role_week"] = np.where(rows["pos"] == "QB", rows["qb_plays"] >= 10, rows["touches"] >= 5)
    # pre-game DFS pool: top recent usage per team / position group
    rows["grp"] = np.where(rows["pos"].isin(["WR", "TE"]), "WRTE", rows["pos"])
    rows = rows.sort_values(["team_id", "grp", "recent_share_raw", "season_share"], ascending=[True, True, False, False])
    rows["grp_rank"] = rows.groupby(["team_id", "grp"]).cumcount() + 1
    rows["pool_dfs"] = rows["grp_rank"] <= rows["grp"].map(POOL_CAPS).fillna(0)
    rows["naive_avg"] = rows["fpg_s"]
    rows["naive_last"] = rows["last_fp"].fillna(0.0)
    # role players with no projection
    act_role = act.copy()
    act_role["role_week"] = np.where(act_role["pass_att"] >= 5, act_role["qb_plays"] >= 10, act_role["touches"] >= 5)
    missed = act_role[act_role["role_week"] & ~act_role.set_index(["athlete_id", "team_id"]).index.isin(
        rows.set_index(["athlete_id", "team_id"]).index)].copy()
    missed["season"], missed["week"] = season, week
    return rows.reset_index(drop=True), missed


def _project_fast(feats, slope, params, pzero):
    """``project`` without quantiles (for the parameter search)."""
    f = feats[feats["appeared_recent"]].copy()
    prior_role = f["pos"].map(lambda p: slope.get(p, slope.get("WR", 0.0))) * f["season_share"]
    wp = params.w_prev * np.minimum(f["n_prev"].fillna(0), params.cap_prev)
    f["base"] = (f["n_s"] * f["fpg_s"] + wp * f["fpg_prev"].fillna(0.0) + params.k0 * prior_role) / (f["n_s"] + wp + params.k0)
    ratio = (f["recent_share"] / f["season_share"].replace(0, np.nan)).fillna(1.0)
    r = np.clip(ratio, params.ratio_lo, params.ratio_hi)
    f["workload"] = np.where(r >= 1.0, r ** params.alpha, r ** params.alpha_down)
    team_avg = ((f["team_n"] * f["team_ppg"].fillna(f["league_ppg"]) + params.k_team * f["league_ppg"]) / (f["team_n"] + params.k_team))
    env = (f["implied"] / team_avg).where(f["implied"].notna() & (team_avg > 0), 1.0)
    f["env"] = np.clip(env, params.env_lo, params.env_hi) ** params.beta
    f["mean_played"] = np.maximum(f["base"] * f["workload"] * f["env"] * f["pos"].map(lambda p: params.scale.get(p, 1.0)), 0.0)
    pzero = pzero or m.DEFAULT_PZERO
    bucket = np.clip(np.searchsorted(m.OPP_BUCKETS, f["exp_opps"].fillna(0).to_numpy(), side="right") - 1, 0, len(m.OPP_BUCKETS) - 2)
    f["p_zero"] = [pzero.get(p, pzero["WR"])["last" if la else "missed_last"][int(b)]
                   for p, la, b in zip(f["pos"], f["appeared_last"], bucket)]
    f["mean"] = (1 - f["p_zero"]) * f["mean_played"]
    return f


def run(data: m.CFBData, season: int, weeks, table_id: str, params: m.CFBParams, dispersion=None, pzero=None,
        feats_cache: dict | None = None, quantiles: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
    R, M = [], []
    for w in weeks:
        f = feats_cache.get((season, w)) if feats_cache is not None else None
        rows, missed = week_rows(data, season, w, table_id, params, dispersion, pzero, feats=f, quantiles=quantiles)
        if len(rows):
            R.append(rows)
        if len(missed):
            M.append(missed)
    return (pd.concat(R, ignore_index=True) if R else pd.DataFrame(),
            pd.concat(M, ignore_index=True) if M else pd.DataFrame())


# --------------------------------------------------------------------------- metrics


def _spearman_by_week(d: pd.DataFrame, pred: str) -> float:
    vals, ns = [], []
    for _, g in d.groupby("week"):
        if len(g) >= 5 and g[pred].nunique() > 1 and g["actual"].nunique() > 1:
            vals.append(spearmanr(g[pred], g["actual"]).statistic)
            ns.append(len(g))
    return float(np.average(vals, weights=ns)) if vals else float("nan")


def mixture_interval(p10, p50, p90, p_zero) -> tuple[float, float]:
    """10th/90th percentiles of the mixture (point mass at zero + played two-piece normal)."""
    lo, hi = sample_from_uniform(np.array([0.1, 0.9]), p10, p50, p90, p_zero)
    return float(lo), float(hi)


def naive_interval(mean: float, pos: str, dispersion) -> tuple[float, float]:
    d = dispersion.get(pos, dispersion["WR"])
    q = quantiles_from_mean_sd(max(mean, 0.0), d["a"] + d["b"] * max(mean, 0.0), d.get("skew", 0.3))
    return q[0], q[2]


def metrics(rows: pd.DataFrame, pool: str, preds: dict[str, str], dispersion=None) -> pd.DataFrame:
    """Per position (and ALL): n, MAE, RMSE, bias (pred - actual), within-week Spearman, 10-90 coverage."""
    d = rows[rows[pool]] if pool in rows.columns else rows
    if pool == "role_week":
        d = d[d["played"]]
    out = []
    for pos in ["QB", "RB", "WR", "TE", "ALL"]:
        g = d if pos == "ALL" else d[d["pos"] == pos]
        if g.empty:
            continue
        for label, col in preds.items():
            e = g[col] - g["actual"]
            row = {"pool": pool, "pos": pos, "predictor": label, "n": int(len(g)), "MAE": float(e.abs().mean()),
                   "RMSE": float(np.sqrt((e ** 2).mean())), "bias": float(e.mean()),
                   "spearman_wk": _spearman_by_week(g, col) if pos != "ALL" else _spearman_by_week(g, col)}
            if dispersion is not None:
                if label == "model":
                    if pool == "role_week":
                        lo, hi = g["p10"], g["p90"]
                    else:
                        iv = [mixture_interval(a, b, c, z) for a, b, c, z in zip(g["p10"], g["p50"], g["p90"], g["p_zero"])]
                        lo, hi = pd.Series([x[0] for x in iv], index=g.index), pd.Series([x[1] for x in iv], index=g.index)
                else:
                    iv = [naive_interval(v, p, dispersion) for v, p in zip(g[col], g["pos"])]
                    lo, hi = pd.Series([x[0] for x in iv], index=g.index), pd.Series([x[1] for x in iv], index=g.index)
                row["cov_10_90"] = float(((g["actual"] >= lo) & (g["actual"] <= hi)).mean())
                row["below_p10"] = float((g["actual"] < lo).mean())
                row["above_p90"] = float((g["actual"] > hi).mean())
            out.append(row)
    return pd.DataFrame(out)


# --------------------------------------------------------------------------- fitting


GRID = {"alpha": [0.5, 0.8, 1.0], "k_recent": [10.0, 20.0, 40.0], "k0": [0.5, 1.0, 2.0],
        "w_prev": [0.1, 0.2, 0.35], "beta": [0.4, 0.7, 1.0]}


def tune(data: m.CFBData, season: int, weeks, table_id: str, base: m.CFBParams | None = None, log=print) -> tuple[m.CFBParams, pd.DataFrame]:
    """Grid search on the pre-game pool's MAE (model mean incl. p_zero). Returns best params and the grid."""
    base = base or m.CFBParams()
    results = []
    for kr in GRID["k_recent"]:
        p_kr = base.with_(k_recent=kr)
        feats = {(season, w): m.features(data, season, w, table_id, p_kr, team_ids=fbs_team_ids(data, season, w)) for w in weeks}
        for a, k0, wp, b in itertools.product(GRID["alpha"], GRID["k0"], GRID["w_prev"], GRID["beta"]):
            p = p_kr.with_(alpha=a, k0=k0, w_prev=wp, beta=b)
            rows, _ = run(data, season, weeks, table_id, p, feats_cache=feats, quantiles=False)
            d = rows[rows["pool_dfs"]]
            e = d["mean"] - d["actual"]
            r = rows[rows["role_week"] & rows["played"]]
            results.append({"k_recent": kr, "alpha": a, "k0": k0, "w_prev": wp, "beta": b,
                            "mae_dfs": float(e.abs().mean()), "rmse_dfs": float(np.sqrt((e ** 2).mean())),
                            "mae_role": float((r["mean_played"] - r["actual"]).abs().mean()), "n": int(len(d))})
        log(f"  tuned k_recent={kr}: best so far MAE {min(x['mae_dfs'] for x in results):.3f}")
    grid = pd.DataFrame(results).sort_values("mae_dfs")
    best = grid.iloc[0]
    return base.with_(k_recent=float(best.k_recent), alpha=float(best.alpha), k0=float(best.k0),
                      w_prev=float(best.w_prev), beta=float(best.beta)), grid


def fit_tables(data: m.CFBData, season: int, weeks, table_id: str, params: m.CFBParams) -> tuple[dict, dict]:
    rows, _ = run(data, season, weeks, table_id, params, quantiles=False)
    pz = m.fit_pzero(rows[["pos", "appeared_last", "exp_opps", "played"]])
    played = rows[rows["played"]][["pos", "mean_played", "actual"]]
    disp = m.fit_dispersion(played)
    return disp, pz


# --------------------------------------------------------------------------- report


def _fmt_table(df: pd.DataFrame, cols: list[str]) -> str:
    head = "| " + " | ".join(cols) + " |\n|" + "|".join("---" for _ in cols) + "|\n"
    body = []
    for _, r in df.iterrows():
        cells = []
        for c in cols:
            v = r[c]
            if isinstance(v, float):
                cells.append("" if math.isnan(v) else (f"{v:.1%}" if c.startswith(("cov", "below", "above")) else f"{v:.2f}"))
            else:
                cells.append(str(v))
        body.append("| " + " | ".join(cells) + " |")
    return head + "\n".join(body) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--table", default="draftkings.cfb")
    ap.add_argument("--fit", action="store_true", help="re-tune parameters and refit tables on 2025 (else use defaults)")
    ap.add_argument("--fit-season", type=int, default=2025)
    ap.add_argument("--fit-weeks", default="3-13")
    ap.add_argument("--test-season", type=int, default=2026)
    ap.add_argument("--test-weeks", default="3-7")
    ap.add_argument("--out", default=str(REPO / "docs" / "results" / "cfb_baseline_backtest.md"))
    ap.add_argument("--json", default=None, help="also write metrics + fitted tables as JSON")
    a = ap.parse_args(argv)
    rng = lambda s: list(range(int(s.split("-")[0]), int(s.split("-")[1]) + 1))
    fit_weeks, test_weeks = rng(a.fit_weeks), rng(a.test_weeks)
    t0 = time.time()
    seasons = sorted({a.fit_season - 1, a.fit_season, a.test_season - 1, a.test_season})
    data = m.CFBData.load(seasons, log=print)
    print(f"loaded {seasons} in {time.time() - t0:.0f}s: {len(data.lines)} player-games")
    params, disp, pz, grid = m.CFBParams(), m.DEFAULT_DISPERSION, m.DEFAULT_PZERO, None
    if a.fit:
        print(f"tuning on {a.fit_season} weeks {a.fit_weeks} ({len(list(itertools.product(*GRID.values())))} combos)")
        params, grid = tune(data, a.fit_season, fit_weeks, a.table)
        print("best params", asdict(params))
        disp, pz = fit_tables(data, a.fit_season, fit_weeks, a.table, params)
        print("dispersion", json.dumps(disp))
        print("p_zero", json.dumps(pz))
    test_rows, test_missed = run(data, a.test_season, test_weeks, a.table, params, disp, pz)
    fit_rows, _ = run(data, a.fit_season, fit_weeks, a.table, params, disp, pz)
    preds = {"model": "mean", "season avg": "naive_avg", "last game": "naive_last"}
    preds_role = {"model": "mean_played", "season avg": "naive_avg", "last game": "naive_last"}
    res = {
        "test_dfs": metrics(test_rows, "pool_dfs", preds, disp),
        "test_role": metrics(test_rows, "role_week", preds_role, disp),
        "fit_dfs": metrics(fit_rows, "pool_dfs", preds, disp),
        "fit_role": metrics(fit_rows, "role_week", preds_role, disp),
    }
    for k, v in res.items():
        print(f"== {k}\n{v.round(3).to_string(index=False)}")
    n_role = int((test_rows["role_week"] & test_rows["played"]).sum())
    report = write_report(Path(a.out), data, a, params, disp, pz, res, test_rows, test_missed, n_role, grid,
                          time.time() - t0)
    print(f"wrote {report}")
    if a.json:
        Path(a.json).write_text(json.dumps({"params": asdict(params), "dispersion": disp, "p_zero": pz,
                                            **{k: v.to_dict("records") for k, v in res.items()}}, indent=1, default=float))
    return 0


def write_report(path: Path, data: m.CFBData, a, params, disp, pz, res, test_rows, test_missed, n_role, grid,
                 elapsed: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = ["pos", "predictor", "n", "MAE", "RMSE", "bias", "spearman_wk", "cov_10_90", "below_p10", "above_p90"]
    v = data.validation
    lines = [f"# CFB baseline backtest ({a.table})", "",
             f"Generated by `python -m dfs.backtest_cfb{' --fit' if a.fit else ''}` on {pd.Timestamp.now(tz='UTC'):%Y-%m-%d %H:%M} UTC "
             f"({elapsed:.0f}s). Every number below is computed from the sportsdataverse release files; none are typed by hand.", "",
             "## Protocol", "",
             f"- Model: `dfs.projections.cfb` (season averages adjusted for recent workload and game environment).",
             f"- Fit: parameters {'grid-searched' if a.fit else 'defaults (from an earlier --fit run)'} and dispersion / p_zero tables fit on "
             f"{a.fit_season} weeks {a.fit_weeks} with {a.fit_season - 1} as the prior season.",
             f"- Test: {a.test_season} weeks {a.test_weeks}, prior season {a.test_season - 1}; out of sample. Each week is projected "
             "from games strictly before it (`dfs.data.cfb.before`).",
             "- Games: FBS-vs-FBS only (what DK/FD college slates contain). Lines: ESPN pre-game spread/total from the betting file "
             "(closing-type lines; a live Wednesday line is noisier).",
             "- Actuals: target-week stat lines from play-by-play (official box used where the pbp is incomplete) scored with "
             "`dfs.scoring`; a projected player with no recorded stat scores 0.",
             "- Pools: **dfs** = per team, the top recent-usage QB1, RB1-2, WR/TE1-4 chosen before kickoff (DNPs count as 0; model "
             "`mean` includes p_zero). **role** = players who recorded >= 5 touches+targets (QB >= 10 dropbacks+carries) and were "
             "projected; model `mean_played` compared; outcome-selected, so its bias column is optimistic for every predictor.",
             "- Naive baselines: season-to-date points per game played, and last game's points. Their 10-90 intervals use the "
             "model's fitted dispersion around the naive mean, so coverage compares like with like.", "",
             "## Parameters", "", "```json", json.dumps(asdict(params), indent=1), "```", "",
             "Dispersion (played SD = a + b*mean, two-piece skew) and p_zero tables:", "", "```json",
             json.dumps(disp, indent=1), json.dumps(pz), "```", ""]
    if grid is not None:
        lines += ["Top of the grid search (fit season, dfs pool):", "", _fmt_table(grid.head(8), list(grid.columns)), ""]
    for key, title in (("test_dfs", f"Test {a.test_season} weeks {a.test_weeks}: dfs pool (pre-game)"),
                       ("test_role", f"Test {a.test_season} weeks {a.test_weeks}: role pool (played)"),
                       ("fit_dfs", f"Fit season {a.fit_season} weeks {a.fit_weeks}: dfs pool (in-sample for parameters)"),
                       ("fit_role", f"Fit season {a.fit_season} weeks {a.fit_weeks}: role pool")):
        lines += [f"## {title}", "", _fmt_table(res[key], cols), ""]
    miss_n = len(test_missed)
    lines += ["## Coverage of the projection set", "",
              f"- Test role-pool player-games projected: {n_role}; role players with no projection (no recorded play in the team's "
              f"last two games, e.g. returning from injury or first appearance): {miss_n} "
              f"({miss_n / max(n_role + miss_n, 1):.1%}).", ""]
    if v:
        lines += ["## Stat-line validation", "",
                  "Play-by-play stat lines vs ESPN's official player box (same games), per season, after repair:", "",
                  "| season | flagged team-games | player-games | FP MAE | FP within 1 pt | pass yds diff | rush yds diff | rec yds diff | TD diff (pass/rush/rec) |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for s, val in sorted(v.items()):
            b = val.get("box_after_repair")
            if not b:
                continue
            st = b["stats"]
            lines.append(f"| {s} | {val.get('flagged_team_games')} | {b['player_games_joined']} | {b['fantasy_points']['mae']:.3f} | "
                         f"{b['fantasy_points']['within_1']:.1%} | {st['pass_yds']['total_diff_pct']:+.2f}% | {st['rush_yds']['total_diff_pct']:+.2f}% | "
                         f"{st['rec_yds']['total_diff_pct']:+.2f}% | {st['pass_td']['total_diff_pct']:+.2f}% / {st['rush_td']['total_diff_pct']:+.2f}% / {st['rec_td']['total_diff_pct']:+.2f}% |")
        lines.append("")
    path.write_text("\n".join(lines))
    return path


if __name__ == "__main__":
    raise SystemExit(main())
