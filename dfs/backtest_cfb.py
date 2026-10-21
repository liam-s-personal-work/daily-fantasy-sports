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


GRID = {"usage_basis": ["all", "competitive"], "k_recent": [10.0, 20.0, 40.0, 80.0, 160.0],
        "alpha": [0.25, 0.5, 0.75, 1.0, 1.25], "alpha_down": [0.0, 0.1, 0.25, 0.5], "k0": [0.0, 0.25, 0.5, 1.0, 2.0],
        "w_prev": [0.0, 0.1, 0.2, 0.35, 0.5], "beta": [0.3, 0.5, 0.7, 0.9, 1.1]}


def _static_frame(data: m.CFBData, season: int, weeks, table_id: str, params: m.CFBParams) -> pd.DataFrame:
    """Per-week rows that do not depend on the mean parameters (features, prior role, actuals, pools)."""
    rows, _ = run(data, season, weeks, table_id, params, quantiles=False)
    slopes = {w: m.role_prior_slope(data, season, w, table_id, params.usage_basis) for w in weeks}
    rows["prior_role"] = [slopes[w].get(p, slopes[w].get("WR", 0.0)) * s
                          for w, p, s in zip(rows["week"], rows["pos"], rows["season_share"])]
    return rows


def mean_played_vec(f: pd.DataFrame, p: m.CFBParams) -> np.ndarray:
    """``project``'s played mean, vectorised over a static frame (identical arithmetic)."""
    wp = p.w_prev * np.minimum(f["n_prev"].fillna(0).to_numpy(float), p.cap_prev)
    base = ((f["n_s"].to_numpy(float) * f["fpg_s"].to_numpy(float) + wp * f["fpg_prev"].fillna(0.0).to_numpy(float)
             + p.k0 * f["prior_role"].to_numpy(float)) / (f["n_s"].to_numpy(float) + wp + p.k0))
    ss = f["season_share"].to_numpy(float)
    ratio = np.where(ss > 0, f["recent_share"].to_numpy(float) / np.where(ss > 0, ss, 1.0), 1.0)
    r = np.clip(ratio, p.ratio_lo, p.ratio_hi)
    work = np.where(r >= 1.0, r ** p.alpha, r ** p.alpha_down)
    league = f["league_ppg"].to_numpy(float)
    team_n = f["team_n"].to_numpy(float)
    team_avg = (team_n * f["team_ppg"].fillna(f["league_ppg"]).to_numpy(float) + p.k_team * league) / (team_n + p.k_team)
    imp = f["implied"].to_numpy(float)
    env = np.where(np.isfinite(imp) & (team_avg > 0), imp / np.where(team_avg > 0, team_avg, 1.0), 1.0)
    env = np.clip(env, p.env_lo, p.env_hi) ** p.beta
    scale = f["pos"].map(dict(p.scale)).fillna(1.0).to_numpy(float) if p.scale else 1.0
    return np.maximum(base * work * env * scale, 0.0)


def tune(data: m.CFBData, season: int, weeks, table_id: str, base: m.CFBParams | None = None, log=print) -> tuple[m.CFBParams, pd.DataFrame]:
    """Grid search for the played-outcome mean: RMSE of ``mean_played`` on the pre-game dfs pool's
    players who then played (RMSE, not MAE: DFS needs expected points, and MAE rewards the lower
    median of right-skewed outcomes). Availability (p_zero) is fit afterwards, separately."""
    base = (base or m.CFBParams()).with_(scale={})
    results = []
    for basis, kr in itertools.product(GRID["usage_basis"], GRID["k_recent"]):
        p_kr = base.with_(k_recent=kr, usage_basis=basis)
        f = _static_frame(data, season, weeks, table_id, p_kr)
        dfs_played = (f["pool_dfs"] & f["played"]).to_numpy()
        role = (f["role_week"] & f["played"]).to_numpy()
        y = f["actual"].to_numpy(float)
        for a, ad, k0, wp, b in itertools.product(GRID["alpha"], GRID["alpha_down"], GRID["k0"], GRID["w_prev"], GRID["beta"]):
            p = p_kr.with_(alpha=a, alpha_down=ad, k0=k0, w_prev=wp, beta=b)
            mp = mean_played_vec(f, p)
            e = mp[dfs_played] - y[dfs_played]
            er = mp[role] - y[role]
            results.append({"usage_basis": basis, "k_recent": kr, "alpha": a, "alpha_down": ad, "k0": k0, "w_prev": wp,
                            "beta": b, "rmse_dfs_played": float(np.sqrt((e ** 2).mean())),
                            "mae_dfs_played": float(np.abs(e).mean()), "bias_dfs_played": float(e.mean()),
                            "rmse_role": float(np.sqrt((er ** 2).mean())), "n": int(dfs_played.sum())})
        log(f"  {basis} k_recent={kr}: best so far RMSE {min(x['rmse_dfs_played'] for x in results):.4f}")
    grid = pd.DataFrame(results).sort_values("rmse_dfs_played").reset_index(drop=True)
    best = grid.iloc[0]
    return base.with_(usage_basis=str(best.usage_basis), k_recent=float(best.k_recent), alpha=float(best.alpha),
                      alpha_down=float(best.alpha_down), k0=float(best.k0), w_prev=float(best.w_prev),
                      beta=float(best.beta), scale=m.CFBParams().scale), grid


def fit_tables(data: m.CFBData, season: int, weeks, table_id: str, params: m.CFBParams) -> tuple[m.CFBParams, dict, dict]:
    """p_zero tables, per-position calibration scale, then dispersion, all on the fit season."""
    rows, _ = run(data, season, weeks, table_id, params.with_(scale={}), quantiles=False)
    pz = m.fit_pzero(rows[["pos", "appeared_last", "exp_opps", "played"]])
    d = rows[rows["pool_dfs"] & rows["played"]]
    scale = {pos: round(float(g["actual"].sum() / g["mean_played"].sum()), 4) for pos, g in d.groupby("pos")}
    params = params.with_(scale=scale)
    rows, _ = run(data, season, weeks, table_id, params, pzero=pz, quantiles=False)
    disp = m.fit_dispersion(rows[rows["played"]][["pos", "mean_played", "actual"]])
    return params, disp, pz


def ablation(data: m.CFBData, season: int, weeks, table_id: str, params: m.CFBParams, disp, pz) -> pd.DataFrame:
    """Each component switched off in turn (dfs pool, model mean incl. p_zero; plus the role-change subset)."""
    zero_pz = {p: {"last": [0.0] * (len(m.OPP_BUCKETS) - 1), "missed_last": [0.0] * (len(m.OPP_BUCKETS) - 1)} for p in m.POSITIONS}
    variants = {"full model": (params, pz), "no workload adjustment": (params.with_(alpha=0.0, alpha_down=0.0), pz),
                "no game environment": (params.with_(beta=0.0), pz),
                "no shrinkage (raw season avg x adj.)": (params.with_(k0=0.0, w_prev=0.0), pz),
                "no p_zero": (params, zero_pz), "no calibration scale": (params.with_(scale={}), pz)}
    out = []
    for name, (p, z) in variants.items():
        rows, _ = run(data, season, weeks, table_id, p, disp, z, quantiles=False)
        d = rows[rows["pool_dfs"]]
        e = d["mean"] - d["actual"]
        ratio = (d["recent_share"] / d["season_share"].replace(0, np.nan)).fillna(1.0)
        up = d[ratio >= 1.4]
        eu = up["mean"] - up["actual"]
        out.append({"variant": name, "n": int(len(d)), "MAE": float(e.abs().mean()), "RMSE": float(np.sqrt((e ** 2).mean())),
                    "bias": float(e.mean()), "spearman_wk": _spearman_by_week(d, "mean"),
                    "n_role_up": int(len(up)), "RMSE_role_up": float(np.sqrt((eu ** 2).mean())) if len(up) else float("nan"),
                    "bias_role_up": float(eu.mean()) if len(up) else float("nan")})
    return pd.DataFrame(out)


def role_change(rows: pd.DataFrame) -> pd.DataFrame:
    """Model vs naive on players whose recent share moved >= 40% from their season share (dfs pool)."""
    d = rows[rows["pool_dfs"]].copy()
    ratio = (d["recent_share"] / d["season_share"].replace(0, np.nan)).fillna(1.0)
    out = []
    for label, sel in (("recent share up >= 40%", ratio >= 1.4), ("recent share down >= 29%", ratio <= 1 / 1.4),
                       ("stable", (ratio < 1.4) & (ratio > 1 / 1.4))):
        g = d[sel]
        for pred, col in (("model", "mean"), ("season avg", "naive_avg"), ("last game", "naive_last")):
            e = g[col] - g["actual"]
            out.append({"subset": label, "predictor": pred, "n": int(len(g)), "MAE": float(e.abs().mean()),
                        "RMSE": float(np.sqrt((e ** 2).mean())), "bias": float(e.mean())})
    return pd.DataFrame(out)


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
        params, disp, pz = fit_tables(data, a.fit_season, fit_weeks, a.table, params)
        print("best params", asdict(params))
        grid_path = Path(a.out).with_name(Path(a.out).stem.replace("backtest", "tuning") + ".csv")
        grid.to_csv(grid_path, index=False, float_format="%.5f")
        print(f"grid -> {grid_path}")
        print("dispersion", json.dumps(disp))
        print("p_zero", json.dumps(pz))
    grid_path = Path(a.out).with_name(Path(a.out).stem.replace("backtest", "tuning") + ".csv")
    if grid is None and grid_path.exists():
        grid = pd.read_csv(grid_path)  # from the last --fit run, for the report
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
    res["ablation"] = ablation(data, a.test_season, test_weeks, a.table, params, disp, pz)
    res["role_change"] = role_change(test_rows)
    print(res["ablation"].round(3).to_string(index=False))
    print(res["role_change"].round(3).to_string(index=False))
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
        lines += [f"Top of the grid search ({len(grid)} combinations; fit season; objective RMSE of the played mean on the "
                  "dfs pool's players who played; full grid in the tuning CSV next to this file):", "",
                  _fmt_table(grid.head(8), list(grid.columns)), ""]
    for key, title in (("test_dfs", f"Test {a.test_season} weeks {a.test_weeks}: dfs pool (pre-game)"),
                       ("test_role", f"Test {a.test_season} weeks {a.test_weeks}: role pool (played)"),
                       ("fit_dfs", f"Fit season {a.fit_season} weeks {a.fit_weeks}: dfs pool (in-sample for parameters)"),
                       ("fit_role", f"Fit season {a.fit_season} weeks {a.fit_weeks}: role pool")):
        lines += [f"## {title}", "", _fmt_table(res[key], cols), ""]
    lines += [f"## Ablation ({a.test_season} weeks {a.test_weeks}, dfs pool, model mean)", "",
              "Each component switched off in turn with everything else fixed. `role_up` = players whose shrunk recent "
              "share is >= 1.4x their season share (new or growing roles).", "",
              _fmt_table(res["ablation"], list(res["ablation"].columns)), "",
              f"## Role changes ({a.test_season} weeks {a.test_weeks}, dfs pool)", "",
              _fmt_table(res["role_change"], list(res["role_change"].columns)), ""]
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
    fum = {s: val["box_after_repair"]["stats"]["fumble_lost"]["total_diff_pct"] for s, val in (v or {}).items()
           if val.get("box_after_repair")}
    fum_txt = ", ".join(f"{s}: {x:+.1f}%" for s, x in sorted(fum.items())) or "n/a"
    lines += ["## Known limitations", "",
              "- Lines: the ESPN betting file holds pre-game lines only for games already played (closing-type). Live slates "
              "have no line in these files, so the environment factor is off unless lines are passed to `CFBProvider(lines=...)`; "
              "backtest accuracy with lines is therefore a little better than a Wednesday projection would see.",
              "- Sacks are scored as rushing yards (NCAA official convention, `SACKS_IN_RUSHING`); not confirmed against a "
              "DK/FD CFB box score. Special-teams fumbles and kneel-downs logged under a team name are not attributed (see "
              f"validation: fumbles lost vs the box {fum_txt}; a few tenths of a point per affected player-game).",
              "- Play-by-play gaps: 2024 incompletion texts often omit the target (target shares from 2024 are partial); "
              "team-games whose pbp is visibly incomplete are scored from the official box and excluded from usage shares.",
              "- The season aggregate files are themselves derived from cfbfastR pbp, so agreeing with them checks parsing "
              "consistency; the ESPN box comparison is the independent check.",
              "- Recent workload adds little on average this early in the season (the last two games are a large share of a "
              "2-6 game season); its value is concentrated in players whose role grew (see role-change table).",
              "- Coverage of the projection set: players with no recorded play in their team's last two games (returning "
              "from injury, debutants) are not projected; first-time starters are invisible to a stats-only baseline.",
              "- Positions come from ESPN rosters (TE kept separate from WR even though DK lists TEs as WR); players without a "
              "roster entry get a usage-inferred position computed over their whole season.", ""]
    path.write_text("\n".join(lines))
    return path


if __name__ == "__main__":
    raise SystemExit(main())
