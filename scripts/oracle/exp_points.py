"""Experiment "points": a direct DK-points model (LightGBM) for the NBA oracle-gap backtest.

Target: actual DK points that night, unconditional (a player who does not play scores 0), so the
regression's prediction is the expected value the lineup solver wants.

Features (all pre-game; nothing from tonight's box score except the separately labelled
last-call column):
- the slate table's engine features: engine, engine_cond, p_zero, minutes, rate, ppg, l5, ppg_prev,
  dk_avg, n_cur, n_prev, moved, last_minutes, benched, absent_last, vacated_min, opp_allowed, home,
  rest_days, b2b;
- recent form from raw ESPN boxes, games strictly before the night's first tip: DK points and
  minutes over the last 3/5/10 games played, DK per minute and usage per minute ((FGA + 0.44 FTA
  + TOV) / min) over the last 10 and 30, DK sd over the last 10, share of the last 5 started,
  started his last box game, days since he last played, minutes and DNP share over his last 5
  box appearances (DNP rows included);
- team context: expected minutes of tonight's active teammates (team total, share, rank), DK
  points and usage per game of players who played the team's last game but are not active
  tonight (vacated_dk, vacated_usg), the opponent's vacated minutes, team and opponent pace
  (possessions per game over the last 15) and points scored / allowed per game.

Protocol: train and tune on season 2025 (2024-25) only; 5 time-contiguous folds over 2025 nights
give out-of-fold predictions (used to fit the blend weight and reported as OOF). Test: 2026, 2027.
A walk-forward variant (retrained at each month start on every night before it) and a last-call
variant (adds tonight's starter flag, public ~30 min before tip) are separate columns.

    python3 scripts/oracle/exp_points.py            # writes data/cache/oracle/exp_points.parquet
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dfs.data import nba  # noqa: E402

SLATES = ROOT / "data/cache/oracle/nba_slates.parquet"
OUT = ROOT / "data/cache/oracle/exp_points.parquet"
EXTRA = ["field_goals_attempted", "free_throws_attempted", "offensive_rebounds", "team_score", "opponent_team_score"]
THREADS = 2
SEED = 7

BASE = ["engine", "engine_cond", "p_zero", "minutes", "rate", "ppg", "l5", "ppg_prev", "dk_avg", "n_cur", "n_prev",
        "moved", "last_minutes", "benched", "absent_last", "vacated_min", "opp_allowed", "home", "rest_days", "b2b"]
FORM = ["dk_l3", "dk_l5", "dk_l10", "min_l3", "min_l5", "min_l10", "dkpm_l10", "dkpm_l30", "usg_pm_l10", "usg_pm_l30",
        "usg_pg_l10", "dk_sd_l10", "start_frac_l5", "started_last", "days_since_played", "app_min_l5", "dnp_frac_l5"]
TEAM = ["team_exp_min", "team_n", "min_rank", "exp_min_share", "vacated_dk", "vacated_usg", "opp_vacated_min",
        "team_poss_l15", "opp_poss_l15", "team_pts_l15", "opp_pts_allowed_l15"]
FEATURES = BASE + FORM + TEAM
LASTCALL = FEATURES + ["starter_tonight"]


# ---------------------------------------------------------------------------------------------- data

def load_box() -> pd.DataFrame:
    """ESPN player boxes 2024-2027 from the local release cache, with FGA / FTA / OREB / team scores."""
    frames = []
    for s in (2024, 2025, 2026, 2027):
        p = nba.cache_path("player_box", s)
        cols = [c for c in nba.BOX_COLUMNS + EXTRA]
        frames.append(pd.read_parquet(p, columns=cols))
    box = nba.normalize(pd.concat(frames, ignore_index=True))
    for c in EXTRA:
        box[c] = pd.to_numeric(box[c], errors="coerce").fillna(0.0)
    box["played"] = ~box["did_not_play"] & (box["minutes"] > 0)
    box["dk"] = np.where(box["played"], nba.dk_points(box), 0.0)
    box["usg"] = box["field_goals_attempted"] + 0.44 * box["free_throws_attempted"] + box["turnovers"]
    box["starter"] = box["starter"].fillna(False).astype(bool)
    box["game_date"] = box["game_date"].astype(str)
    return box.sort_values(["start", "game_id", "athlete_id"]).reset_index(drop=True)


def night_cutoffs(box: pd.DataFrame) -> pd.Series:
    """slate date -> first regular-season tip that night (the slate builder's cutoff)."""
    return box[box["season_type"] == 2].groupby("game_date")["start"].min()


def _asof(left: pd.DataFrame, right: pd.DataFrame, by: str, cols: list[str], suffix: str = "") -> pd.DataFrame:
    """For each left row (by, cutoff): right's state at its last row with start strictly before cutoff."""
    L = left[[by, "cutoff"]].reset_index().rename(columns={"index": "_row"}).sort_values("cutoff")
    R = right[[by, "start"] + cols].sort_values("start")
    m = pd.merge_asof(L, R, left_on="cutoff", right_on="start", by=by, direction="backward",
                      allow_exact_matches=False)
    m = m.set_index("_row").reindex(left.index)
    out = m[cols + ["start"]].rename(columns={c: c + suffix for c in cols + ["start"]})
    return out


def player_logs(box: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Rolling state per player after each game: P over games played, F over every box appearance."""
    P = box[box["played"]].copy()
    g = P.groupby("athlete_id", sort=False)

    def roll(col: str, n: int, fn: str = "mean") -> pd.Series:
        r = g[col].rolling(n, min_periods=1)
        return getattr(r, fn)().reset_index(level=0, drop=True)

    for n in (3, 5, 10):
        P[f"dk_l{n}"] = roll("dk", n)
        P[f"min_l{n}"] = roll("minutes", n)
    for n in (10, 30):
        dk_s, min_s, usg_s = roll("dk", n, "sum"), roll("minutes", n, "sum"), roll("usg", n, "sum")
        P[f"dkpm_l{n}"] = dk_s / min_s.clip(lower=1.0)
        P[f"usg_pm_l{n}"] = usg_s / min_s.clip(lower=1.0)
    P["usg_pg_l10"] = roll("usg", 10)
    P["dk_sd_l10"] = roll("dk", 10, "std")
    P["start_frac_l5"] = g["starter"].rolling(5, min_periods=1).mean().reset_index(level=0, drop=True)
    F = box.copy()
    gf = F.groupby("athlete_id", sort=False)
    F["app_min_l5"] = gf["minutes"].rolling(5, min_periods=1).mean().reset_index(level=0, drop=True)
    F["dnp_frac_l5"] = (~F["played"]).astype(float).groupby(F["athlete_id"]).rolling(5, min_periods=1).mean() \
        .reset_index(level=0, drop=True)
    F["started_last"] = F["starter"].astype(float)
    return P, F


def team_log(box: pd.DataFrame) -> pd.DataFrame:
    t = box.groupby(["game_id", "team_abbreviation"]).agg(
        start=("start", "first"), fga=("field_goals_attempted", "sum"), fta=("free_throws_attempted", "sum"),
        tov=("turnovers", "sum"), oreb=("offensive_rebounds", "sum"), pts=("team_score", "first"),
        allowed=("opponent_team_score", "first")).reset_index()
    t["poss"] = t["fga"] + 0.44 * t["fta"] + t["tov"] - t["oreb"]
    t = t.sort_values("start")
    g = t.groupby("team_abbreviation", sort=False)
    for c in ("poss", "pts", "allowed"):
        t[f"{c}_l15"] = g[c].rolling(15, min_periods=1).mean().reset_index(level=0, drop=True)
    return t


def build_features(d: pd.DataFrame, box: pd.DataFrame) -> pd.DataFrame:
    d = d.copy()
    cut = night_cutoffs(box)
    d["cutoff"] = d["slate"].map(cut)
    assert d["cutoff"].notna().all()
    P, F = player_logs(box)
    pc = ["dk_l3", "dk_l5", "dk_l10", "min_l3", "min_l5", "min_l10", "dkpm_l10", "dkpm_l30", "usg_pm_l10",
          "usg_pm_l30", "usg_pg_l10", "dk_sd_l10", "start_frac_l5"]
    a = _asof(d, P, "athlete_id", pc)
    d[pc] = a[pc]
    d["days_since_played"] = (d["cutoff"] - a["start"]).dt.total_seconds() / 86400
    b = _asof(d, F, "athlete_id", ["app_min_l5", "dnp_frac_l5", "started_last"])
    d[["app_min_l5", "dnp_frac_l5", "started_last"]] = b[["app_min_l5", "dnp_frac_l5", "started_last"]]
    assert (d["days_since_played"].dropna() > 0).all()

    # team context from tonight's active pool (pre-game columns only)
    d["exp_min"] = d["minutes"] * (1 - d["p_zero"])
    grp = d.groupby(["slate", "team"])
    d["team_exp_min"] = grp["exp_min"].transform("sum")
    d["team_n"] = grp["exp_min"].transform("size")
    d["min_rank"] = grp["minutes"].rank(ascending=False, method="first")
    d["exp_min_share"] = d["exp_min"] / d["team_exp_min"].clip(lower=1.0)
    opp_vac = d.groupby(["slate", "team"])["vacated_min"].first()
    d["opp_vacated_min"] = opp_vac.reindex(pd.MultiIndex.from_arrays([d["slate"], d["opp"]])).fillna(0.0).to_numpy()

    # vacated DK / usage: players who played the team's last game (before the cutoff) but are not active tonight
    T = team_log(box)
    st = d[["slate", "team", "cutoff"]].drop_duplicates(["slate", "team"]).rename(columns={"team": "team_abbreviation"})
    st = st.reset_index(drop=True)
    last = _asof(st, T, "team_abbreviation", ["game_id"])
    st["last_gid"] = last["game_id"]
    played = box[box["played"]][["game_id", "team_abbreviation", "athlete_id"]]
    st = st.dropna(subset=["last_gid"]).astype({"last_gid": "int64"})
    gone = st.merge(played, left_on=["last_gid", "team_abbreviation"],
                                               right_on=["game_id", "team_abbreviation"])
    active = set(zip(d["slate"], d["athlete_id"]))
    gone = gone[[(s, a) not in active for s, a in zip(gone["slate"], gone["athlete_id"])]].reset_index(drop=True)
    gs = _asof(gone, P, "athlete_id", ["dk_l10", "usg_pg_l10"])
    gone[["dk_l10", "usg_pg_l10"]] = gs[["dk_l10", "usg_pg_l10"]].fillna(0.0)
    vac = gone.groupby(["slate", "team_abbreviation"])[["dk_l10", "usg_pg_l10"]].sum()
    key = pd.MultiIndex.from_arrays([d["slate"], d["team"]])
    d["vacated_dk"] = vac["dk_l10"].reindex(key).fillna(0.0).to_numpy()
    d["vacated_usg"] = vac["usg_pg_l10"].reindex(key).fillna(0.0).to_numpy()

    # pace and scoring, last 15 games (any season)
    tl = _asof(d.rename(columns={"team": "team_abbreviation"}), T, "team_abbreviation", ["poss_l15", "pts_l15"])
    d["team_poss_l15"], d["team_pts_l15"] = tl["poss_l15"].to_numpy(), tl["pts_l15"].to_numpy()
    ol = _asof(d.drop(columns="team").rename(columns={"opp": "team_abbreviation"}), T, "team_abbreviation",
               ["poss_l15", "allowed_l15"])
    d["opp_poss_l15"], d["opp_pts_allowed_l15"] = ol["poss_l15"].to_numpy(), ol["allowed_l15"].to_numpy()

    # LAST-CALL ONLY: tonight's starter flag (public ~30 minutes before tip); never in the main model
    tonight = box[box["season_type"] == 2][["game_date", "athlete_id", "team_abbreviation", "starter"]] \
        .drop_duplicates(["game_date", "athlete_id", "team_abbreviation"])
    d = d.merge(tonight.rename(columns={"game_date": "slate", "team_abbreviation": "team", "starter": "starter_tonight"}),
                on=["slate", "athlete_id", "team"], how="left")
    d["starter_tonight"] = d["starter_tonight"].fillna(False).astype(float)
    return d


# ---------------------------------------------------------------------------------------------- model

def params(**kw) -> dict:
    p = dict(objective="regression", metric="rmse", learning_rate=0.03, num_leaves=31, min_child_samples=100, feature_fraction=0.8,
             bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, num_threads=THREADS, seed=SEED, verbose=-1)
    p.update(kw)
    return p


def time_folds(slates: pd.Series, k: int = 5) -> list[tuple[np.ndarray, np.ndarray]]:
    nights = np.array(sorted(slates.unique()))
    blocks = np.array_split(nights, k)
    out = []
    for b in blocks:
        te = slates.isin(b).to_numpy()
        out.append((np.where(~te)[0], np.where(te)[0]))
    return out


def cv_rounds(X, y, folds, p, max_rounds=3000) -> tuple[int, float]:
    ds = lgb.Dataset(X, y, free_raw_data=False)
    r = lgb.cv(p, ds, num_boost_round=max_rounds, folds=folds, stratified=False, shuffle=False,
               callbacks=[lgb.early_stopping(100, verbose=False)])
    key = [k for k in r if k.endswith("-mean")][0]
    return len(r[key]), float(r[key][-1])


def oof(X, y, folds, p, n) -> np.ndarray:
    out = np.full(len(y), np.nan)
    for tr, te in folds:
        m = lgb.train(p, lgb.Dataset(X.iloc[tr], y.iloc[tr]), num_boost_round=n)
        out[te] = m.predict(X.iloc[te])
    return out


def metrics(pred: np.ndarray, y: np.ndarray) -> str:
    e = pred - y
    return f"MAE {np.abs(e).mean():.3f}  RMSE {np.sqrt((e ** 2).mean()):.3f}  bias {e.mean():+.3f}"


def main() -> None:
    t0 = time.time()
    d = pd.read_parquet(SLATES)
    box = load_box()
    d = build_features(d, box)
    print(f"features built: {len(d)} rows, {time.time() - t0:.0f}s", flush=True)
    real = d.groupby(["season", "team"])["slate"].transform("nunique") >= 3  # drop All-Star exhibition rows from training
    d["allstar"] = ~real & d["season"].isin([2025, 2026])
    tr_mask = (d["season"] == 2025) & ~d["allstar"]
    tr = d[tr_mask].reset_index(drop=True)
    X, y = tr[FEATURES], tr["actual"]
    folds = time_folds(tr["slate"])

    # small grid on 2025 CV (L2)
    best = None
    for nl in (15, 31, 63):
        for mcs in (50, 200):
            p = params(num_leaves=nl, min_child_samples=mcs)
            n, score = cv_rounds(X, y, folds, p)
            print(f"  cv num_leaves={nl} min_child_samples={mcs}: rounds {n} rmse {score:.4f}", flush=True)
            if best is None or score < best[2]:
                best = (p, n, score)
    P_, N_, _ = best
    print(f"chosen: num_leaves={P_['num_leaves']} min_child_samples={P_['min_child_samples']} rounds={N_}", flush=True)

    tr["oof_gbm"] = oof(X, y, folds, P_, N_)
    # stacked blend: w * gbm + (1 - w) * engine, least squares on 2025 OOF, w in [0, 1]
    e, g, a = tr["engine"].to_numpy(), tr["oof_gbm"].to_numpy(), y.to_numpy()
    w = float(np.clip(np.dot(a - e, g - e) / np.dot(g - e, g - e), 0, 1))
    print(f"blend weight on gbm (2025 OOF): {w:.3f}", flush=True)

    full = lgb.train(P_, lgb.Dataset(X, y), num_boost_round=N_)
    imp = pd.Series(full.feature_importance("gain"), index=FEATURES).sort_values(ascending=False)
    print("top features (gain share):", ", ".join(f"{k} {v / imp.sum():.3f}" for k, v in imp.head(15).items()))

    # quantiles
    q = {}
    for alpha in (0.1, 0.9):
        pq_ = params(objective="quantile", metric="quantile", alpha=alpha, num_leaves=P_["num_leaves"],
                     min_child_samples=P_["min_child_samples"])
        nq, _ = cv_rounds(X, y, folds, pq_)
        q[alpha] = (pq_, nq, lgb.train(pq_, lgb.Dataset(X, y), num_boost_round=nq))
        print(f"  quantile {alpha}: rounds {nq}", flush=True)

    # last-call tier (adds tonight's starter flag)
    Xl = tr[LASTCALL]
    nl_, _ = cv_rounds(Xl, y, folds, P_)
    tr["oof_lastcall"] = oof(Xl, y, folds, P_, nl_)
    lastcall = lgb.train(P_, lgb.Dataset(Xl, y), num_boost_round=nl_)
    print(f"  last-call rounds {nl_}", flush=True)

    # predictions for every row
    d["points_gbm"] = full.predict(d[FEATURES])
    d["points_p10"] = q[0.1][2].predict(d[FEATURES])
    d["points_p90"] = q[0.9][2].predict(d[FEATURES])
    d["points_lastcall"] = lastcall.predict(d[LASTCALL])
    # honest 2025 values: out-of-fold
    idx = np.where(tr_mask)[0]
    d.loc[idx, "points_gbm"] = tr["oof_gbm"].to_numpy()
    d.loc[idx, "points_lastcall"] = tr["oof_lastcall"].to_numpy()

    # walk-forward: retrain at each month start of the test seasons on every (non-exhibition) night before it
    d["points_gbm_wf"] = np.nan
    month = d["slate"].str[:7]
    for mo in sorted(month[d["season"].isin([2026, 2027])].unique()):
        hist = d[(d["slate"] < f"{mo}-01") & ~d["allstar"]]
        m = lgb.train(P_, lgb.Dataset(hist[FEATURES], hist["actual"]), num_boost_round=N_)
        sel = (month == mo) & d["season"].isin([2026, 2027])
        d.loc[sel, "points_gbm_wf"] = m.predict(d.loc[sel, FEATURES])
        print(f"  walk-forward {mo}: trained on {len(hist)} rows, predicted {int(sel.sum())}", flush=True)

    for c in ("points_gbm", "points_lastcall", "points_gbm_wf", "points_p10", "points_p90"):
        d[c] = d[c].clip(lower=0.0)
    d["points_avg"] = 0.5 * (d["engine"] + d["points_gbm"])
    d["points_blend"] = w * d["points_gbm"] + (1 - w) * d["engine"]
    d["points_oof"] = tr_mask.astype(int)  # 1 = 2025 row whose points_gbm / blend / lastcall are out-of-fold

    # player-level accuracy
    cols = ["engine", "points_gbm", "points_avg", "points_blend", "points_gbm_wf", "points_lastcall"]
    for name, m in (("2025 OOF (train season)", tr_mask), ("test 2026+2027", d["season"].isin([2026, 2027])),
                    ("test 2026", d["season"] == 2026), ("test 2027", d["season"] == 2027)):
        s = d[m]
        print(f"\n{name}: n={len(s)}")
        for c in cols:
            if s[c].notna().all():
                print(f"  {c:16s} {metrics(s[c].to_numpy(), s['actual'].to_numpy())}")
        if name.startswith("test"):
            print(f"  p10/p90 coverage: below p10 {np.mean(s['actual'] < s['points_p10']):.3f}, "
                  f"above p90 {np.mean(s['actual'] > s['points_p90']):.3f}")
        s15 = s[s["engine"] >= 25]
        print(f"  engine>=25 subset n={len(s15)}: " + "; ".join(
            f"{c} RMSE {np.sqrt(((s15[c] - s15['actual']) ** 2).mean()):.3f}" for c in cols if s15[c].notna().all()))

    out = d.groupby(["slate", "athlete_id"], as_index=False)[
        ["points_gbm", "points_avg", "points_blend", "points_gbm_wf", "points_lastcall", "points_p10", "points_p90",
         "points_oof"]].mean()
    out.to_parquet(OUT)
    print(f"\nwrote {OUT} ({len(out)} rows), {time.time() - t0:.0f}s total")


if __name__ == "__main__":
    main()
