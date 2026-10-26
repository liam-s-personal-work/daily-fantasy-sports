"""Fit a DraftKings-like CFB classic salary model on the two real DK CFB files.

DK prices college players from what it can see (season-to-date and last season's DK points per
game, recent games, who is starting, the betting line), not from our projection model, so the
pseudo-salaries built here give the historical oracle slates realistic mispricing to exploit or miss.

Training files (2026 week 8, real DK CFB classic slates, loaded with dfs.io.contest_file.load_contest):
- data/cache/real_samples/DKEntries_CFB_2026-10-23.csv  (Friday, 5 games, 374 players)
- data/cache/real_samples/DKEntries_CFB_2026-10-24.csv  (Saturday main, 12 games, 851 players)
Players are matched to sportsdataverse (ESPN) athlete ids with the live provider's matcher
(dfs.projections.cfb_provider.CFBProvider: slate games -> team ids, then name on the team).

DK positions: QB, RB, WR. DK lists tight ends as WR, so ESPN TEs are priced (and slotted) as WR.

Model, per DK position, fit separately:
    salary = clip(a + b * blend + extras, lo, hi), rounded to $100
    blend    = w * ppg + (1 - w) * ppg_prev, w = n / (n + k)   (season to date vs last season)
    extras   = from the candidates l3 (the last-3-games average blended the same way), starter (rank 1 in
               the team's DK position group by recent usage share, the stand-in for the depth chart DK
               sees) with starter x blend, and for QBs the team's implied points
DK points per game count games with a recorded stat line (all opponents, FBS and FCS) strictly
before the week; last season includes its bowl games. Fit by robust least squares *through the
clip* (floor-priced players are censored); the feature set and k are picked per position by
leave-one-game-out MAE over both files (17 games). Chosen (cfb_salary_model.json): QB and RB
blend + l3 + starter (k = 1), WR blend + starter (k = 0.5); the QB implied total did not help.
Players with no game in either season get the median salary of their position in the files (the
floor). ``lo``/``hi`` = DK's min/max per position across the two files (QB 4500-9700, RB 4000-9700,
WR 3000-9300). DK's own FPPG in the files tracks our season-to-date DK points per game (r = 0.97).

Lines: week-8 implied points come from the cached betting file, else from a refreshed copy
(data/cache/oracle/cfb_ref/betting_2026.parquet, ESPN closing lines downloaded 2026-10-26).

Caveat: both files are from one week (players have 1-7 games this season), so the season vs
last-season weighting is identified only from players with few games; week-2 prices in 2024-2026
(1 game played) are an extrapolation of week-8 behaviour.

    python3 scripts/oracle/cfb_salary.py          # writes scripts/oracle/cfb_salary_model.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import least_squares

from dfs.data import cfb as cfbdata

ROOT = Path(__file__).resolve().parents[2]
MODEL_PATH = Path(__file__).with_name("cfb_salary_model.json")
TABLE = "draftkings.cfb"
FILES = [ROOT / "data/cache/real_samples/DKEntries_CFB_2026-10-23.csv",
         ROOT / "data/cache/real_samples/DKEntries_CFB_2026-10-24.csv"]
REF_BETTING = ROOT / "data/cache/oracle/cfb_ref/betting_2026.parquet"
POSITIONS = ("QB", "RB", "WR")
DK_POS = {"QB": "QB", "RB": "RB", "WR": "WR", "TE": "WR"}
K_GRID = (0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 13.0, 21.0)
# candidate extra-feature sets, chosen per position by leave-one-game-out MAE
SPECS = {
    "ppg": [],
    "ppg+l3": ["l3"],
    "ppg+l3+starter": ["l3", "starter", "starter_x_blend"],
    "ppg+starter": ["starter", "starter_x_blend"],
    "ppg+l3+starter+implied": ["l3", "starter", "starter_x_blend", "implied"],
    "ppg+starter+implied": ["starter", "starter_x_blend", "implied"],
}
QB_ONLY = {"implied"}


def dk_pos(pos) -> str:
    return DK_POS.get(str(pos), "WR")


# --------------------------------------------------------------------------- features DK can see


def dk_features(data, season: int, week: int) -> pd.DataFrame:
    """Per athlete_id: DK points per game this season (ppg, n) and last season (ppg_prev, n_prev), and
    the last-3-games average (l3, across both seasons), from stat lines strictly before (season, week)."""
    key = ("oracle_dk_features", season, week)
    if key in data.cache:
        return data.cache[key]
    L = data.lines[["season", "week", "game_id", "athlete_id"]].assign(fp=data.points(TABLE))
    h = cfbdata.before(L, season, week, min_season=season - 1).sort_values(["season", "week", "game_id"], kind="stable")
    cur, prev = h[h["season"] == season], h[h["season"] == season - 1]
    f = pd.DataFrame({"ppg": cur.groupby("athlete_id")["fp"].mean(), "n": cur.groupby("athlete_id").size()})
    f = f.join(pd.DataFrame({"ppg_prev": prev.groupby("athlete_id")["fp"].mean(),
                             "n_prev": prev.groupby("athlete_id").size()}), how="outer")
    f["l3"] = h.groupby("athlete_id").tail(3).groupby("athlete_id")["fp"].mean()
    f["n"] = f["n"].fillna(0).astype(int)
    f["n_prev"] = f["n_prev"].fillna(0).astype(int)
    f.index.name = "athlete_id"
    data.cache[key] = f
    return f


def blend(f: pd.DataFrame, k: float, col: str = "ppg") -> pd.Series:
    """w * col + (1 - w) * ppg_prev with w = n / (n + k); either side alone when the other is missing."""
    w = f["n"] / (f["n"] + k)
    cur, prev = f[col], f["ppg_prev"]
    out = w * cur + (1 - w) * prev
    return out.where(cur.notna() & prev.notna(), cur.fillna(prev))


def has_history(f: pd.DataFrame) -> pd.Series:
    return f["ppg"].notna() | f["ppg_prev"].notna()


# --------------------------------------------------------------------------- model


def _design(f: pd.DataFrame, k: float, extra: list[str]) -> np.ndarray:
    b = blend(f, k)
    cols = [np.ones(len(f)), b.to_numpy(float)]
    for e in extra:
        if e == "l3":
            cols.append(blend(f, k, "l3").fillna(b).to_numpy(float))
        elif e == "starter_x_blend":
            cols.append(f["starter"].astype(float).fillna(0.0).to_numpy() * b.to_numpy(float))
        elif e == "implied":
            # team implied points, centred on a typical 28; a missing line counts as average
            cols.append((f["implied"].astype(float) - 28.0).fillna(0.0).to_numpy())
        else:
            cols.append(f[e].astype(float).fillna(0.0).to_numpy())
    return np.column_stack(cols)


def _fit_pos(X: np.ndarray, y: np.ndarray, lo: float, hi: float) -> np.ndarray:
    coef0, *_ = np.linalg.lstsq(X, y, rcond=None)
    res = least_squares(lambda c: np.clip(X @ c, lo, hi) - y, coef0, loss="soft_l1", f_scale=500.0)
    return res.x


def predict(f: pd.DataFrame, model: dict, pos) -> np.ndarray:
    """DK-like salaries for rows of ``f`` (dk_features columns plus ``starter`` (0/1) and ``implied``),
    aligned with ``pos`` (DK positions QB/RB/WR; ESPN TE is mapped to WR)."""
    f = f.reset_index(drop=True)
    pos = np.asarray([dk_pos(p) for p in pos], dtype=object)
    out = np.full(len(f), np.nan)
    hist = has_history(f).to_numpy()
    for p in np.unique(pos):
        m = model["positions"].get(p)
        if m is None:
            continue
        i = np.where((pos == p) & hist)[0]
        if len(i):
            X = _design(f.iloc[i], m["k"], m["extra"])
            out[i] = np.clip(X @ np.asarray(m["coef"]), *m["range"])
    miss = np.where(np.isnan(out))[0]
    if len(miss):
        out[miss] = [model["no_history"].get(pos[i], model["positions"].get(pos[i], {"range": [3000, 0]})["range"][0])
                     for i in miss]
    return (np.round(out / 100.0) * 100).astype(int)


# --------------------------------------------------------------------------- training data


def _week_implied(data, season: int, week: int) -> dict[int, float]:
    """team_id -> implied points for (season, week): the cached lines, else the refreshed betting file."""
    tg = data.team_games[(data.team_games["season"] == season) & (data.team_games["week"] == week)]
    out = {int(t): float(v) for t, v in zip(tg["team_id"], tg["implied"]) if pd.notna(v)}
    if REF_BETTING.exists():
        b = pd.read_parquet(REF_BETTING)
        b = b[b["game_id"].isin(set(tg["game_id"]))].drop_duplicates("game_id")
        hs = pd.to_numeric(b["home_team_spread"], errors="coerce")
        tot = pd.to_numeric(b["over_under"], errors="coerce")
        hi, ai = cfbdata.implied_points(hs, tot)
        imp = {}
        for gid, h, a in zip(b["game_id"], hi, ai):
            g = tg[tg["game_id"] == gid]
            for t, is_home in zip(g["team_id"], g["is_home"]):
                v = h if is_home == 1 else a
                if np.isfinite(v):
                    imp[int(t)] = float(v)
        out = {**imp, **out}
    return out


def training_rows(data, files: list[Path] | None = None) -> pd.DataFrame:
    """One row per real-file player (matched to an ESPN athlete id where possible) with DK-visible features."""
    from dfs.io.contest_file import load_contest
    from dfs.projections import cfb as m
    from dfs.projections.cfb_provider import CFBProvider

    rows = []
    for path in files or FILES:
        c = load_contest(str(path))
        site, gt = (str(getattr(x, "value", x)) for x in (c.rules.site, c.rules.game_type))
        assert site == "draftkings" and gt == "classic" and c.rules.sport == "cfb", (path, site, gt)
        prov = CFBProvider(data=data)
        prov.prepare(c)  # matches games -> team ids -> players (point in time); projections unused
        gids = [g for g in prov.team_report.games.values()]
        sched = data.schedules.set_index("game_id")
        season = int(sched.loc[gids, "season"].mode().iloc[0])
        week = int(sched.loc[gids, "week"].mode().iloc[0])
        team_ids = {mt.team_id for mt in prov.team_report.matches.values() if mt.team_id is not None}
        feats = dk_features(data, season, week)
        fw = m.features(data, season, week, TABLE, m.CFBParams(), team_ids=team_ids)
        depth = dict(zip(zip(fw["athlete_id"], fw["team_id"]), fw["depth"]))
        espn_pos = dict(zip(fw["athlete_id"], fw["pos"]))
        imp = _week_implied(data, season, week)
        sal = {cd.player_key: cd.salary for cd in c.candidates}
        for k, p in c.players.items():
            mt = prov.player_matches.get(k)
            aid, tid = (mt["athlete_id"], mt["team_id"]) if mt else (None, prov.team_report.team_id(p.team))
            fr = feats.loc[aid].to_dict() if aid is not None and aid in feats.index else \
                {"ppg": np.nan, "n": 0, "ppg_prev": np.nan, "n_prev": 0, "l3": np.nan}
            dep = depth.get((aid, tid), np.nan)
            rows.append({"file": path.name, "season": season, "week": week, "key": k, "athlete_id": aid, "team_id": tid,
                         "name": p.name, "team": p.team, "game": p.game, "pos": p.positions[0],
                         "pos_espn": espn_pos.get(aid), "salary": sal[k], "site_avg": p.site_avg, "depth": dep,
                         "starter": float(dep == 1), "implied": imp.get(int(tid)) if tid is not None else np.nan,
                         "matched": aid is not None, **fr})
        print(f"{path.name}: {season} wk{week}, {len(c.players)} players, {len(prov.player_matches)} matched, "
              f"{len(c.games)} games", file=sys.stderr)
    d = pd.DataFrame(rows)
    d["n"] = d["n"].fillna(0).astype(int)
    d["n_prev"] = d["n_prev"].fillna(0).astype(int)
    return d


# --------------------------------------------------------------------------- fit + report


def _metrics(y, pred) -> dict:
    y, pred = np.asarray(y, float), np.asarray(pred, float)
    if len(y) == 0:
        return {"r2": None, "mae": None, "n": 0}
    ss = ((y - y.mean()) ** 2).sum()
    return {"r2": round(float(1 - ((y - pred) ** 2).sum() / ss), 4) if ss > 0 else None,
            "mae": round(float(np.mean(np.abs(y - pred))), 1), "n": int(len(y))}


def _fit_predict(tr: pd.DataFrame, te: pd.DataFrame, k: float, extra: list[str], lo: float, hi: float) -> np.ndarray:
    c = _fit_pos(_design(tr, k, extra), tr["salary"].to_numpy(float), lo, hi)
    return np.round(np.clip(_design(te, k, extra) @ c, lo, hi), -2)


def _cv(g: pd.DataFrame, by: str, k: float, extra: list[str], lo: float, hi: float) -> np.ndarray:
    """Out-of-group predictions (leave one ``by`` group out)."""
    pred = np.full(len(g), np.nan)
    for v in g[by].unique():
        te = (g[by] == v).to_numpy()
        if te.all():
            continue
        pred[te] = _fit_predict(g[~te], g[te], k, extra, lo, hi)
    return pred


def ranges(d: pd.DataFrame) -> dict[str, tuple[int, int]]:
    return {p: (int(g["salary"].min()), int(g["salary"].max())) for p, g in d.groupby("pos")}


def fit(d: pd.DataFrame) -> dict:
    files = sorted(d["file"].unique())
    rng = ranges(d)
    model = {"table": TABLE, "files": files, "positions": {}, "no_history": {}, "ranges": rng}
    report: dict = {}
    hist = d[has_history(d)].reset_index(drop=True)
    for pos in POSITIONS:
        g = hist[hist["pos"] == pos].reset_index(drop=True)
        lo, hi = rng[pos]
        y = g["salary"].to_numpy(float)
        grid = []
        for name, extra in SPECS.items():
            if pos != "QB" and QB_ONLY & set(extra):
                continue
            for k in K_GRID:
                pr = _cv(g, "game", k, extra, lo, hi)
                grid.append({"spec": name, "k": k, "logo_mae": float(np.mean(np.abs(pr - y)))})
        grid = pd.DataFrame(grid).sort_values("logo_mae").reset_index(drop=True)
        best = grid.iloc[0]
        extra, k = SPECS[best["spec"]], float(best["k"])
        coef = _fit_pos(_design(g, k, extra), y, lo, hi)
        model["positions"][pos] = {"k": k, "spec": best["spec"], "extra": extra,
                                   "coef": [round(float(x), 4) for x in coef],
                                   "coef_names": ["const", "blend", *extra], "range": [lo, hi]}
        pred = predict(g, model, g["pos"]).astype(float)
        logo = _cv(g, "game", k, extra, lo, hi)
        above = y > lo
        pl = g["pool_like"].to_numpy(bool)
        rep = {"spec": best["spec"], "k": k, "in_sample": _metrics(y, pred), "above_floor": _metrics(y[above], pred[above]),
               "leave_one_game_out": _metrics(y, logo),
               "leave_one_game_out_above_floor": _metrics(y[above], logo[above]),
               "pool_like_in_sample": _metrics(y[pl], pred[pl]),
               "pool_like_leave_one_game_out": _metrics(y[pl], logo[pl]),
               "spec_grid_top": grid.groupby("spec")["logo_mae"].min().round(1).sort_values().to_dict()}
        for f in files:  # holdout across the two files: fit on the other file, score this one
            tr, te = g[g["file"] != f], g[g["file"] == f]
            if len(tr) >= 10 and len(te):
                rep[f"holdout_score_{f}"] = _metrics(te["salary"], _fit_predict(tr, te, k, extra, lo, hi))
                te_pool = te[te["pool_like"]]
                rep[f"holdout_score_{f}_pool_like"] = _metrics(te_pool["salary"], _fit_predict(tr, te_pool, k, extra, lo, hi)) \
                    if len(te_pool) else None
        report[pos] = rep
    for pos, g in d.groupby("pos"):
        model["no_history"][pos] = float(np.round(g.loc[~has_history(g), "salary"].median(), -2)) if (~has_history(g)).any() \
            else float(rng[pos][0])
    allp = predict(hist, model, hist["pos"])
    report["ALL"] = _metrics(hist["salary"], allp)
    pl = hist["pool_like"].to_numpy(bool)
    report["ALL_pool_like"] = _metrics(hist["salary"].to_numpy()[pl], allp[pl])
    report["rows_by_file"] = d.groupby("file").size().to_dict()
    report["matched_by_file"] = d.groupby("file")["matched"].mean().round(4).to_dict()
    report["rows_with_history"] = int(len(hist))
    report["rows_without_history"] = int(len(d) - len(hist))
    report["site_avg_vs_our_ppg"] = {
        "corr": round(float(d[["site_avg", "ppg"]].dropna().corr().iloc[0, 1]), 4),
        "median_abs_diff": round(float((d["site_avg"] - d["ppg"]).abs().median()), 3)}
    report["dk_pos_vs_espn_pos"] = {f"{a}|{b}": int(n) for (a, b), n in
                                    d.groupby(["pos", d["pos_espn"].fillna("none")]).size().items()}
    report["notes"] = ("pool_like = rank 1 QB, 1-2 RB, 1-4 WR/TE of the team by recent usage (the oracle slates' "
                       "pre-game pool). holdout_score_<file> = fit on the other file only, scored on this one. "
                       "leave_one_game_out = refit without each game's players (17 games), used to pick spec and k.")
    model["fit"] = report
    return model


def mark_pool_like(d: pd.DataFrame) -> pd.DataFrame:
    """The oracle slates' pool: QB1, RB1-2, WR/TE1-4 by recent usage (ESPN position group)."""
    cap = {"QB": 1, "RB": 2, "WR": 4, "TE": 4}
    c = d["pos_espn"].map(cap)
    return d.assign(pool_like=(d["depth"] <= c).fillna(False).astype(bool))


if __name__ == "__main__":
    from dfs.projections import cfb as m

    data = m.CFBData.load([2025, 2026], max_age_hours=1e9)  # cache only: no refresh mid-run
    d = mark_pool_like(training_rows(data))
    model = fit(d)
    print(json.dumps(model["fit"], indent=1))
    MODEL_PATH.write_text(json.dumps(model, indent=1))
    print(f"wrote {MODEL_PATH}", file=sys.stderr)
