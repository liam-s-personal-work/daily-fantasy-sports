"""Experiment "cfb gbm": a LightGBM correction on top of the CFB engine, scored by the oracle gap.

Training rows: every FBS player-game in the pre-game DFS pool, ``dfs.backtest_cfb.week_rows`` rows with
``pool_dfs`` (per team QB1, RB1-2, WR/TE1-4 by usage over the team's last two games, chosen before kickoff;
a pool player who then records no stat scores 0), for 2024 weeks 2-14 (2023 = prior season), 2025 weeks
2-14 and 2026 weeks 2-8, built exactly as scripts/oracle/cfb_slates.py builds its slate rows (same data,
CFBParams(), DEFAULT_DISPERSION, DEFAULT_PZERO). The 33 oracle slates are a subset of these rows; the
merge on (slate, player_id) is checked to reproduce the slate table's engine column exactly.

Target: actual DK points, unconditional (0 for a pool player with no recorded stat).

Features (pre-game only; actual, fp, played, touches, qb_plays, carries, targets, pass_att, stat_source and
role_week are target-week outcomes and never used): engine (unconditional mean), engine_cond, p_zero,
p10, p90, naive_avg, naive_last, dk_l3 (last 3 games, both seasons), n_s, n_prev, fpg_prev, usage shares
(season, recent shrunk, recent raw), recent/season opportunities, recent appearances, exp_opps,
team_opps_pg, depth (group rank), appeared_last, weeks since the last game, the engine's components
(prior_role, base, workload, env, team_avg_shrunk), team_ppg, team_n, league_ppg, implied, opp_implied,
spread, total, line missing, home, neutral, kickoff hour ET, Power-4 and AP rank of team and opponent,
opp_pos_allowed (cfb_slates.opp_pos_allowed), week, position (categorical), plus within-team features over
the team's pool rows (team engine sum, the player's engine share and engine rank in the QB / RB / WR+TE
group, the team QB1's engine, the group's summed recent share).

Models (one model for all positions; 3-seed average):
- direct: LightGBM L2 regression on actual;           column cfbgbm_direct
- resid:  LightGBM L2 regression on actual - engine;  cfbgbm_resid = engine + resid_hat
- shrunk: engine + w * resid_hat, w = least-squares weight on 2025 out-of-fold rows, clipped to [0, 1]
          (cfbgbm_resid_w); engine + a * (direct - engine) likewise (cfbgbm_direct_w);
- cfbgbm_resid_wl: w picked by mean % of optimal on the 13 2025 slates (out-of-fold), a noisy sensitivity;
- cfbgbm_ens = (direct + resid) / 2;
- controls / diagnostics: cfbgbm_lincal (per-position OLS actual ~ a + b * engine, same blocks) and the
  single-seed models cfbgbm_{direct,resid}_s{0,1,2} (lineup noise from model randomness alone).
Configuration (leaves, min rows per leaf, monotone engine constraint for direct) and rounds are chosen by
2025 week-grouped CV: 4 contiguous blocks of 2025 weeks, each held out with training on 2024 + the rest.

Protocol:
- final test: 2026 weeks 2-8, models trained on 2024 + 2025 rows only;
- 2025 out-of-fold: leave-one-week-out over 2025 weeks 2-14, each fold trained on 2024 + the other 2025
  weeks (later weeks included: not walk-forward). The engine's own parameters were fit on 2025 weeks
  3-13, so 2025 rows are in-sample for the engine and its residuals look smaller there than out of sample;
- 2024 out-of-fold (secondary): leave-one-week-out over 2024, each fold trained on 2025 + the other 2024
  weeks (trains on the following season). 2024 is out of sample for the engine.

Lineups: dfs.oracle.evaluate_slate with Rules('cfb') on data/cache/oracle/cfb_slates.parquet (pseudo-salaries)
with 2 spawned worker processes; paired bootstrap (dfs.oracle.paired_bootstrap) vs engine per season.

    python3 scripts/oracle/exp_cfb_gbm.py   # -> data/cache/oracle/exp_cfb_gbm.parquet, res_cfb_gbm.csv
"""

from __future__ import annotations

import multiprocessing as mp
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))
from dfs.oracle import Rules, evaluate_slate, paired_bootstrap  # noqa: E402

SLATES = ROOT / "data/cache/oracle/cfb_slates.parquet"
OUT = ROOT / "data/cache/oracle/exp_cfb_gbm.parquet"
RES = ROOT / "data/cache/oracle/res_cfb_gbm.csv"
WEEKS = {2024: range(2, 15), 2025: range(2, 15), 2026: range(2, 9)}
TRAIN, TEST = [2024, 2025], 2026
THREADS, WORKERS = 2, 2
SEEDS = [7, 11, 23]
T0 = time.time()

NUM = ["engine", "engine_cond", "p_zero", "p10", "p90", "naive_avg", "naive_last", "dk_l3", "n_s", "n_prev",
       "fpg_prev", "season_share", "recent_share", "recent_share_raw", "recent_opps", "recent_team_opps",
       "recent_appear", "season_opps", "exp_opps", "team_opps_pg", "depth", "appeared_last", "weeks_since",
       "prior_role", "base", "workload", "env", "team_avg_shrunk", "team_ppg", "team_n", "league_ppg", "implied",
       "opp_implied", "spread", "total", "line_missing", "home", "neutral", "kick_et", "p4", "opp_p4", "rank",
       "opp_rank", "opp_pos_allowed", "week"]
DERIVED = ["team_eng", "grp_eng_share", "eng_rank", "qb1_eng", "grp_share_sum"]
CAT = ["pos"]
FEATURES = NUM + DERIVED + CAT
POST_GAME = {"actual", "fp", "played", "touches", "qb_plays", "carries", "targets", "pass_att", "stat_source",
             "role_week", "resid", "played_actual"}
assert not POST_GAME & set(FEATURES)


def log(msg: str) -> None:
    print(f"[{time.time() - T0:6.0f}s] {msg}", file=sys.stderr, flush=True)


# ------------------------------------------------------------------------------------------- rows

def build_rows() -> pd.DataFrame:
    """All FBS pool_dfs player-games, cfb_slates' engine and features, plus derived pre-game features."""
    import cfb_slates as cs
    from dfs.backtest_cfb import week_rows

    data = cs.load_data()
    games = cs._games_frame(data.schedules, data.game_lines).drop_duplicates("game_id").set_index("game_id")
    out = []
    for season, weeks in WEEKS.items():
        for week in weeks:
            rows, _ = week_rows(data, season, week, cs.TABLE, cs.PARAMS, cs.DISP, cs.PZ)
            if rows.empty:
                continue
            r = rows[rows["pool_dfs"]].drop_duplicates("athlete_id").reset_index(drop=True)
            home = r["is_home"].astype(int) == 1
            gi = games.reindex(r["game_id"].to_numpy())
            d = pd.DataFrame({
                "slate": f"{season}-w{week:02d}", "season": season, "week": week,
                "player_id": r["athlete_id"].astype("int64"), "team_id": r["team_id"].astype("int64"),
                "game_id": r["game_id"].astype("int64"), "pos": r["pos"].astype(str),
                "actual": r["actual"].astype(float),
                "engine": r["mean"].astype(float), "engine_cond": r["mean_played"].astype(float),
                "p_zero": r["p_zero"].astype(float), "p10": r["p10"].astype(float), "p90": r["p90"].astype(float),
                "naive_avg": r["naive_avg"].astype(float), "naive_last": r["naive_last"].astype(float),
                "n_s": r["n_s"].astype(float), "n_prev": r["n_prev"].astype(float), "fpg_prev": r["fpg_prev"].astype(float),
                "season_share": r["season_share"], "recent_share": r["recent_share"],
                "recent_share_raw": r["recent_share_raw"], "recent_opps": r["recent_opps"],
                "recent_team_opps": r["recent_team_opps"], "recent_appear": r["recent_appear"].astype(float),
                "season_opps": r["season_opps"], "exp_opps": r["exp_opps"], "team_opps_pg": r["team_opps_pg"],
                "depth": r["depth"].astype(float), "appeared_last": r["appeared_last"].astype(float),
                "weeks_since": (week - r["last_week"]).astype(float), "prior_role": r["prior_role"],
                "base": r["base"], "workload": r["workload"], "env": r["env"], "team_avg_shrunk": r["team_avg_shrunk"],
                "team_ppg": r["team_ppg"], "team_n": r["team_n"].astype(float), "league_ppg": r["league_ppg"],
                "implied": r["implied"].astype(float), "opp_implied": (r["total"] - r["implied"]).astype(float),
                "spread": np.where(home, r["home_spread"], -r["home_spread"]).astype(float),
                "total": r["total"].astype(float), "line_missing": r["implied"].isna().astype(float),
                "home": home.astype(float), "neutral": r["neutral_site"].fillna(False).astype(float)})
            kick = pd.to_datetime(r["start_date"], utc=True, errors="coerce").dt.tz_convert("America/New_York")
            d["kick_et"] = (kick.dt.hour + kick.dt.minute / 60.0).to_numpy()
            hp, ap = gi["home_p4"].to_numpy(float), gi["away_p4"].to_numpy(float)
            hr, ar = gi["home_rank"].to_numpy(float), gi["away_rank"].to_numpy(float)
            d["p4"], d["opp_p4"] = np.where(home, hp, ap), np.where(home, ap, hp)
            d["rank"], d["opp_rank"] = np.where(home, hr, ar), np.where(home, ar, hr)
            allowed = cs.opp_pos_allowed(data, season, week)
            grp = np.where(d["pos"].isin(["WR", "TE"]), "WRTE", d["pos"])
            d["opp_pos_allowed"] = [allowed.get((int(o), g), 1.0) for o, g in zip(r["opp_id"], grp)]
            f = cs.cfb_salary.dk_features(data, season, week).reindex(d["player_id"].to_numpy())
            d["dk_l3"] = f["l3"].to_numpy(float)
            d["grp"] = grp
            out.append(d)
    d = pd.concat(out, ignore_index=True)
    # within-team features over the team's pool rows (all pre-game)
    key = ["season", "week", "team_id"]
    d["team_eng"] = d.groupby(key)["engine"].transform("sum")
    gsum = d.groupby(key + ["grp"])["engine"].transform("sum")
    d["grp_eng_share"] = d["engine"] / gsum.replace(0, np.nan)
    d["eng_rank"] = d.groupby(key + ["grp"])["engine"].rank(ascending=False, method="first")
    qb1 = d[d["pos"] == "QB"].groupby(key)["engine"].max().rename("qb1_eng")
    d = d.join(qb1, on=key)
    d["grp_share_sum"] = d.groupby(key + ["grp"])["recent_share"].transform("sum")
    d["pos"] = pd.Categorical(d["pos"], categories=["QB", "RB", "WR", "TE"])
    d["resid"] = d["actual"] - d["engine"]
    return d.drop(columns=["grp"])


# ----------------------------------------------------------------------------------------- models

BASE = dict(objective="regression", learning_rate=0.05, feature_fraction=0.7, bagging_fraction=0.8, bagging_freq=1,
            max_bin=63, min_data_per_group=50, cat_smooth=10, verbose=-1, num_threads=THREADS)


def grid(direct: bool) -> list[dict]:
    return [dict(num_leaves=nl, min_data_in_leaf=md, lambda_l2=l2, mono=mono)
            for nl in (4, 8, 16) for md in (50, 200) for l2 in (10.0,) for mono in ((False, True) if direct else (False,))]


def lgb_params(cfg: dict, seed: int) -> dict:
    p = {**BASE, **{k: v for k, v in cfg.items() if k != "mono"}, "seed": seed}
    if cfg.get("mono"):
        p["monotone_constraints"] = [1 if f == "engine" else 0 for f in FEATURES]
        p["monotone_constraints_method"] = "advanced"
    return p


def fit(X: pd.DataFrame, y, cfg: dict, rounds: int, seed: int):
    import lightgbm as lgb
    return lgb.train(lgb_params(cfg, seed), lgb.Dataset(X, y, categorical_feature=CAT), num_boost_round=rounds)


def predict_seeds(tr: pd.DataFrame, target: str, te: pd.DataFrame, cfg: dict, rounds: int) -> np.ndarray:
    """(len(SEEDS), len(te)) predictions, one model per seed."""
    return np.array([fit(tr[FEATURES], tr[target], cfg, rounds, s).predict(te[FEATURES]) for s in SEEDS])


def blocks(d: pd.DataFrame) -> list[tuple[pd.Index, pd.Index]]:
    """(train index, predict index) blocks: 2025 and 2024 leave-one-week-out (train = the other season + the
    season's other weeks), then 2026 from all of 2024-2025."""
    tv = d[d["season"].isin(TRAIN)]
    out = [(tv.index[a], tv.index[b]) for season, other in ((2025, 2024), (2024, 2025)) for a, b in folds(tv, season, other)]
    return out + [(tv.index, d.index[d["season"] == TEST])]


def folds(d: pd.DataFrame, season: int, other: int) -> list[tuple[np.ndarray, np.ndarray]]:
    """Leave-one-week-out over ``season``: train = all of ``other`` + the season's other weeks (row positions in d)."""
    s, o = (d["season"] == season).to_numpy(), (d["season"] == other).to_numpy()
    wk = d["week"].to_numpy()
    return [(np.flatnonzero(o | (s & (wk != w))), np.flatnonzero(s & (wk == w))) for w in sorted(set(wk[s]))]


TUNE_BLOCKS = [(2, 3, 4), (5, 6, 7), (8, 9, 10), (11, 12, 13, 14)]


def tune(d: pd.DataFrame, target: str, direct: bool) -> tuple[dict, int, pd.DataFrame]:
    """Config and rounds by 2025 week-grouped CV: 4 contiguous blocks of 2025 weeks (TUNE_BLOCKS), each held out
    in turn with training on 2024 + the other 2025 blocks; mean held-out MSE."""
    import lightgbm as lgb

    tr = d[d["season"].isin(TRAIN)].reset_index(drop=True)
    s25, wk = (tr["season"] == 2025).to_numpy(), tr["week"].to_numpy()
    fo = [(np.flatnonzero(~(s25 & np.isin(wk, b))), np.flatnonzero(s25 & np.isin(wk, b))) for b in TUNE_BLOCKS]
    rows = []
    for cfg in grid(direct):
        ds = lgb.Dataset(tr[FEATURES], tr[target], categorical_feature=CAT, free_raw_data=False)
        cv = lgb.cv({**lgb_params(cfg, SEEDS[0]), "metric": "l2"}, ds, num_boost_round=3000, folds=fo,
                    callbacks=[lgb.early_stopping(150, verbose=False)])
        k = next(k for k in cv if k.endswith("l2-mean"))
        rows.append({**cfg, "iters": len(cv[k]), "cv_mse": float(cv[k][-1])})
        log(f"  {target} {cfg}: iters {len(cv[k])} cv_mse {cv[k][-1]:.4f}")
    g = pd.DataFrame(rows).sort_values("cv_mse").reset_index(drop=True)
    b = g.iloc[0]
    cfg = dict(num_leaves=int(b["num_leaves"]), min_data_in_leaf=int(b["min_data_in_leaf"]),
               lambda_l2=float(b["lambda_l2"]), mono=bool(b["mono"]))
    return cfg, int(b["iters"]), g


def run_model(d: pd.DataFrame, target: str, direct: bool) -> tuple[pd.DataFrame, dict]:
    """Per-seed predictions of ``target`` for every row (columns s0..s2): 2025 and 2024 leave-one-week-out, 2026
    from the 2024-2025 fit."""
    cfg, iters, g = tune(d, target, direct)
    log(f"{target}: best {cfg} iters={iters}\n{g.head(5).to_string(index=False)}")
    pred = pd.DataFrame(np.nan, index=d.index, columns=[f"s{k}" for k in range(len(SEEDS))])
    for tr_i, te_i in blocks(d):
        pred.loc[te_i] = predict_seeds(d.loc[tr_i], target, d.loc[te_i], cfg, iters).T
    log(f"  {target}: all blocks done")
    tv = d[d["season"].isin(TRAIN)]
    imp = pd.Series(fit(tv[FEATURES], tv[target], cfg, iters, SEEDS[0]).feature_importance("gain"), index=FEATURES)
    imp = (imp / imp.sum()).sort_values(ascending=False)
    return pred, {"cfg": cfg, "iters": iters, "grid": g, "importance": imp}


def lincal(d: pd.DataFrame) -> pd.Series:
    """Control: per-position OLS actual ~ a + b * engine, same train/predict blocks as the GBMs."""
    out = pd.Series(np.nan, index=d.index)
    for tr_i, te_i in blocks(d):
        tr, te = d.loc[tr_i], d.loc[te_i]
        for p, g in tr.groupby("pos", observed=True):
            b, a = np.polyfit(g["engine"], g["actual"], 1)
            k = te.index[te["pos"] == p]
            out[k] = a + b * d.loc[k, "engine"]
    return out


def ls_weight(x: np.ndarray, y: np.ndarray) -> float:
    """argmin_w mean((y - w x)^2), clipped to [0, 1]."""
    return float(np.clip((x * y).sum() / max((x * x).sum(), 1e-12), 0.0, 1.0))


# ------------------------------------------------------------------------------------- evaluation

def _eval_one(args):
    g, cols = args
    g = g.reset_index(drop=True).copy()
    g["pos"] = g["pos"].map(lambda s: set(s.split(",")) if isinstance(s, str) else s)
    r = evaluate_slate(g, cols, Rules("cfb"))
    return {"slate": g["slate"].iloc[0], "season": int(g["season"].iloc[0]), **r}


def evaluate(s: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    keep = ["slate", "season", "pos", "salary", "game", "actual", *cols]
    items = [(g[keep], cols) for _, g in s.groupby("slate")]
    with ProcessPoolExecutor(WORKERS, mp_context=mp.get_context("spawn")) as ex:
        return pd.DataFrame(list(ex.map(_eval_one, items)))


def lineup_report(res: pd.DataFrame, cols: list[str], seasons: list[int], title: str) -> list[str]:
    r = res[res["season"].isin(seasons)]
    L = [f"\n== {title}: {len(r)} slates ==",
         f"{'column':18s} {'%opt':>7s} {'pts':>7s} {'ovl':>5s}   vs engine, pp of optimal [95% CI]   vs engine, points [95% CI]"]
    for c in cols:
        line = f"{c:18s} {100 * r[f'{c}:pct'].mean():7.2f} {r[f'{c}:pts'].mean():7.1f} {r[f'{c}:overlap'].mean():5.2f}"
        if c != "engine":
            m, lo, hi = paired_bootstrap(100 * r[f"{c}:pct"].to_numpy(), 100 * r["engine:pct"].to_numpy())
            mp_, lp, hp = paired_bootstrap(r[f"{c}:pts"].to_numpy(), r["engine:pts"].to_numpy())
            line += f"   {m:+6.2f} [{lo:+6.2f}, {hi:+6.2f}]          {mp_:+6.1f} [{lp:+6.1f}, {hp:+6.1f}]"
        L.append(line)
    L.append(f"{'optimal':18s} {'':7s} {r['optimal'].mean():7.1f}")
    return L


def player_report(d: pd.DataFrame, cols: list[str], mask: pd.Series, title: str) -> list[str]:
    t = d[mask]
    L = [f"\n== player level, {title}, n={len(t)} ==",
         f"{'column':18s} {'MAE':>6s} {'RMSE':>6s} {'bias':>6s}   MAE-engine [95% week-cluster CI]   RMSE-engine [95% CI]"]
    wk = t["slate"].to_numpy()
    groups = {w: np.flatnonzero(wk == w) for w in np.unique(wk)}
    keys = list(groups)
    rng = np.random.default_rng(0)
    boots = [np.concatenate([groups[k] for k in rng.choice(keys, len(keys))]) for _ in range(2000)]
    y = t["actual"].to_numpy()
    e0 = t["engine"].to_numpy() - y
    for c in cols:
        e = t[c].to_numpy() - y
        line = f"{c:18s} {np.abs(e).mean():6.3f} {np.sqrt((e ** 2).mean()):6.3f} {e.mean():+6.2f}"
        if c != "engine":
            da = np.abs(e) - np.abs(e0)
            ba = [da[b].mean() for b in boots]
            br = [np.sqrt((e[b] ** 2).mean()) - np.sqrt((e0[b] ** 2).mean()) for b in boots]
            line += (f"   {da.mean():+6.3f} [{np.percentile(ba, 2.5):+.3f}, {np.percentile(ba, 97.5):+.3f}]"
                     f"        {np.sqrt((e ** 2).mean()) - np.sqrt((e0 ** 2).mean()):+6.3f} "
                     f"[{np.percentile(br, 2.5):+.3f}, {np.percentile(br, 97.5):+.3f}]")
        L.append(line)
    L.append("MAE by position:")
    L.append(t.groupby("pos", observed=True).apply(
        lambda g: pd.Series({c: (g[c] - g["actual"]).abs().mean() for c in cols} | {"n": len(g)}),
        include_groups=False).round(3).to_string())
    return L


# ------------------------------------------------------------------------------------------- main

def main() -> None:
    d = build_rows()
    log(f"{len(d)} pool rows: " + ", ".join(f"{s} {n}" for s, n in d.groupby("season").size().items())
        + f"; slates {d['slate'].nunique()} weeks")
    infos = {}
    pd_, infos["direct"] = run_model(d, "actual", True)
    pr_, infos["resid"] = run_model(d, "resid", False)
    d["_direct"], d["_resid_hat"] = pd_.mean(axis=1), pr_.mean(axis=1)
    seed_cols = []
    for k in pd_.columns:  # single-seed models: how much of a lineup difference is model randomness
        d[f"cfbgbm_direct_{k}"], d[f"cfbgbm_resid_{k}"] = pd_[k], d["engine"] + pr_[k]
        seed_cols += [f"cfbgbm_direct_{k}", f"cfbgbm_resid_{k}"]
    d["cfbgbm_lincal"] = lincal(d)

    v = d[d["season"] == 2025]  # 2025 out-of-fold rows
    w = ls_weight(v["_resid_hat"].to_numpy(), v["resid"].to_numpy())
    a = ls_weight((v["_direct"] - v["engine"]).to_numpy(), v["resid"].to_numpy())
    ws = np.round(np.arange(0, 1.01, 0.1), 2)
    curve = {x: float(((v["resid"] - x * v["_resid_hat"]) ** 2).mean()) for x in ws}
    curve_a = {x: float(((v["resid"] - x * (v["_direct"] - v["engine"])) ** 2).mean()) for x in ws}
    log(f"2025 OOF MSE by resid weight: " + ", ".join(f"{x}:{m:.3f}" for x, m in curve.items()) + f" -> w={w:.3f}")
    log(f"2025 OOF MSE by direct weight: " + ", ".join(f"{x}:{m:.3f}" for x, m in curve_a.items()) + f" -> a={a:.3f}")
    d["cfbgbm_direct"] = d["_direct"]
    d["cfbgbm_resid"] = d["engine"] + d["_resid_hat"]
    d["cfbgbm_resid_w"] = d["engine"] + w * d["_resid_hat"]
    d["cfbgbm_direct_w"] = d["engine"] + a * (d["_direct"] - d["engine"])
    d["cfbgbm_ens"] = 0.5 * (d["cfbgbm_direct"] + d["cfbgbm_resid"])

    # merge onto the oracle slates (key: slate + player_id) and check the engine reproduces exactly
    s = pd.read_parquet(SLATES)
    pre_cols = ["cfbgbm_lincal", "cfbgbm_direct", "cfbgbm_resid", "cfbgbm_resid_w", "cfbgbm_direct_w", "cfbgbm_ens"]
    m = s.merge(d[["slate", "player_id", "engine", "_resid_hat", *pre_cols, *seed_cols]].rename(columns={"engine": "engine_chk"}),
                on=["slate", "player_id"], how="left", validate="one_to_one")
    miss = int(m["engine_chk"].isna().sum())
    gap = float((m["engine"] - m["engine_chk"]).abs().max())
    log(f"slate merge: {len(m)} slate rows, {miss} without a pool row, engine max |diff| {gap:.2e}")
    assert miss == 0 and gap < 1e-9, (miss, gap)

    # lineup-chosen w (2025 out-of-fold slates), a sensitivity column
    wl_cols = [f"wl{x}" for x in (0.0, 0.25, 0.5, 0.75, 1.0)]
    m25 = m[m["season"] == 2025].copy()
    for c in wl_cols:
        m25[c] = m25["engine"] + float(c[2:]) * m25["_resid_hat"]
    r25 = evaluate(m25, wl_cols)
    wl_curve = {float(c[2:]): 100 * r25[f"{c}:pct"].mean() for c in wl_cols}
    wl = max(wl_curve, key=lambda x: (round(wl_curve[x], 6), -abs(x - 0.5)))
    log("2025 OOF % of optimal by resid weight: " + ", ".join(f"{x}:{p:.2f}" for x, p in wl_curve.items()) + f" -> wl={wl}")
    for frame in (d, m):
        frame["cfbgbm_resid_wl"] = frame["engine"] + wl * frame["_resid_hat"]
    pre_cols.append("cfbgbm_resid_wl")

    o = d[["slate", "season", "week", "player_id", "team_id", *pre_cols, *seed_cols]].copy()
    o["fold"] = np.where(o["season"] == TEST, "test_fit_2024_2025",
                         np.where(o["season"] == 2025, "oof_2025_loWo", "oof_2024_loWo_with_2025"))
    o["in_slate"] = o.set_index(["slate", "player_id"]).index.isin(s.set_index(["slate", "player_id"]).index)
    o["w_resid"], o["a_direct"], o["wl_resid"] = w, a, wl
    o.to_parquet(OUT, index=False)
    log(f"wrote {OUT}")

    cols = ["engine", "naive_avg", *pre_cols]
    res = evaluate(m, cols + seed_cols)
    res.to_csv(RES, index=False)
    log(f"wrote {RES}")

    # real DK salaries on 2026-w08 (DK's own prices; one slate)
    rd = m[m["salary_dk"].notna()].assign(salary=lambda x: x["salary_dk"].astype(int))
    rdk = evaluate(rd, cols) if len(rd) else pd.DataFrame()

    L = [f"rows: {len(d)} FBS pool player-games ({', '.join(f'{k} {n}' for k, n in d.groupby('season').size().items())})",
         f"resid weight w (2025 OOF least squares) = {w:.3f}; direct weight a = {a:.3f}; lineup-picked wl = {wl}",
         "2025 OOF MSE vs w: " + ", ".join(f"{x}:{mm:.3f}" for x, mm in curve.items()),
         "2025 OOF % of optimal vs w: " + ", ".join(f"{x}:{p:.2f}" for x, p in wl_curve.items())]
    for k, inf in infos.items():
        L.append(f"{k}: cfg {inf['cfg']} iters {inf['iters']}; top gain: "
                 + ", ".join(f"{f} {g:.3f}" for f, g in inf["importance"].head(12).items()))
    L += lineup_report(res, cols, [2026], "TEST 2026 weeks 2-8 (trained on 2024-2025)")
    L += lineup_report(res, cols, [2025], "2025 out-of-fold (leave-one-week-out; engine in-sample)")
    L += lineup_report(res, cols, [2024], "2024 out-of-fold (leave-one-week-out, trained with 2025)")
    L += lineup_report(res, cols, [2024, 2025, 2026], "ALL 33 slates (mixed protocols)")
    L.append("\n== single-seed models: mean % of optimal minus engine (pp) by season [2026 test / 2025 OOF / 2024 OOF] ==")
    for c in ["cfbgbm_direct", "cfbgbm_resid"]:
        for c2 in [c] + [x for x in seed_cols if x.startswith(c + "_s")]:
            L.append(f"{c2:20s} " + " / ".join(
                f"{100 * (res.loc[res['season'] == y, f'{c2}:pct'] - res.loc[res['season'] == y, 'engine:pct']).mean():+6.2f}"
                for y in (2026, 2025, 2024)))
    if len(rdk):
        L.append("\nreal DK salaries 2026-w08: optimal {:.1f}; ".format(rdk["optimal"].iloc[0]) + ", ".join(
            f"{c} {100 * rdk[f'{c}:pct'].iloc[0]:.1f}% ({rdk[f'{c}:pts'].iloc[0]:.1f})" for c in cols))
    pcols = ["engine", "naive_avg", *pre_cols]
    L += player_report(d, pcols, d["season"] == TEST, "2026 all FBS pool rows (test)")
    L += player_report(m, pcols, m["season"] == TEST, "2026 slate rows (test)")
    L += player_report(d, pcols, d["season"] == 2025, "2025 all FBS pool rows (OOF)")
    L += player_report(d, pcols, d["season"] == 2024, "2024 all FBS pool rows (OOF)")
    print("\n".join(L))


if __name__ == "__main__":
    main()
