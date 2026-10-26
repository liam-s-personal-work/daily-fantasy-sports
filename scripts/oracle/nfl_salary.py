"""Fit a DraftKings-like NFL classic salary model on real DK main-slate files.

DK sets salaries from what it can see (season-to-date and last season's DK points per game, recent
games, the depth chart, the betting lines), not from our projection model, so pseudo-salaries built
here give our projections realistic mispricing to exploit or miss.

Training files (2026, real DK classic main slates):
- week 6: data/cache/real_samples/dk_classic_wk6_main_18Oct2026.csv
- week 3: runs/replays_v2/dr-nfl-classic-20261021-164401-96363ba7/contest.json (parsed from
  data/cache/real_samples/DKEntriesClassic.csv). Note: the e99c8420 replay holds the week-6 file
  again, not week 3.

Model, per position (QB/RB/WR/TE/DST), fit separately:
    salary = clip(a + b * blend + c * l3_blend + extras, lo, hi), rounded to $100
    blend    = w * ppg + (1 - w) * ppg_prev, w = n / (n + k)   (season-to-date vs last season)
    l3_blend = same with the last-3-games average in place of ppg
    extras   = QB/RB/WR/TE: depth-chart starter dummy and starter x blend (chart as of ~6 days before
               the Sunday, when DK sets prices; see dk_depth); DST: own and opponent implied points
fit by robust least squares *through the clip* (players priced at the floor are censored, so plain
OLS would flatten the slope); k is picked per position by leave-one-file-out MAE. Players with no
game in either season get the median salary of their position and (DK-timed) depth-chart bucket
(starter / backup / other) in the training files.

    python3 scripts/oracle/nfl_salary.py          # writes scripts/oracle/nfl_salary_model.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import least_squares

from dfs.data import nflverse as nv
from dfs.data.teams import canonicalize
from dfs.models import Contest
from dfs.projections.matching import match_players

ROOT = Path(__file__).resolve().parents[2]
MODEL_PATH = Path(__file__).with_name("nfl_salary_model.json")
TABLE = "draftkings.nfl"
FILES = [
    (ROOT / "data/cache/real_samples/dk_classic_wk6_main_18Oct2026.csv", "csv"),
    (ROOT / "runs/replays_v2/dr-nfl-classic-20261021-164401-96363ba7/contest.json", "json"),
]
POSITIONS = ("QB", "RB", "WR", "TE", "DST")
# Floors are DK's (identical in both 2026 files). Caps sit above the 2026 maxima (QB 8000, RB 8800, WR 8600,
# TE 7500, DST 3800) because 2021-2023 DK prices reached ~$9.5k for the top RB/WR.
RANGE = {"QB": (4000, 9000), "RB": (4000, 9500), "WR": (3000, 9500), "TE": (2500, 8000), "DST": (2000, 4500)}
# starter_x_blend lets DK's price respond to production mainly for depth-chart starters (a backup QB with a
# big last season stays near the floor); it lowers the above-floor leave-one-file-out MAE at every position.
_P = ["starter", "starter_x_blend"]
EXTRA = {"QB": _P, "RB": _P, "WR": _P, "TE": _P, "DST": ["implied", "opp_implied"]}
K_GRID = (0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 13.0, 21.0, 34.0)


# --------------------------------------------------------------------------- features DK can see


def _reg_game_ids(data) -> set:
    key = ("oracle_reg_game_ids",)
    if key not in data.cache:
        s = data.schedules
        data.cache[key] = set(s.loc[s["game_type"] == "REG", "game_id"])
    return data.cache[key]


def dk_features(data, season: int, week: int) -> pd.DataFrame:
    """Per player id (gsis_id, or DST:<team>): DK points per game this season (ppg, n) and last season
    (ppg_prev, n_prev) and the last-3-games average (l3), regular-season games strictly before
    (season, week). Needs ``dfs.backtest.add_actual_points(data)`` to have run."""
    reg = _reg_game_ids(data)
    pg = nv.before(data.player_games, season, week, min_season=season - 1)
    pg = pg[pg["game_id"].isin(reg)]
    pg = pg[(pg["offense_pct"].fillna(0) > 0) | ((pg["targets"] + pg["carries"] + pg["attempts"]) > 0)]
    p = pd.DataFrame({"id": pg["gsis_id"], "season": pg["season"], "wk": pg["wk"], "fp": pg[f"fp_{TABLE}"]})
    tg = nv.before(data.team_games, season, week, min_season=season - 1)
    tg = tg[tg["played"] & tg["game_id"].isin(reg)]
    d = pd.DataFrame({"id": "DST:" + tg["team"], "season": tg["season"],
                      "wk": nv.week_key(tg["season"], tg["week"]), "fp": tg[f"dst_{TABLE}"]})
    h = pd.concat([p, d], ignore_index=True).sort_values("wk", kind="stable")
    cur, prev = h[h["season"] == season], h[h["season"] == season - 1]
    f = pd.DataFrame({"ppg": cur.groupby("id")["fp"].mean(), "n": cur.groupby("id").size()})
    f = f.join(pd.DataFrame({"ppg_prev": prev.groupby("id")["fp"].mean(), "n_prev": prev.groupby("id").size()}),
               how="outer")
    f["l3"] = h.groupby("id")["fp"].apply(lambda s: s.tail(3).mean())
    f["n"] = f["n"].fillna(0).astype(int)
    f["n_prev"] = f["n_prev"].fillna(0).astype(int)
    f.index.name = "player_id"
    return f


def dk_depth(data, season: int, week: int) -> dict[tuple[str, str], str]:
    """(gsis_id, pos) -> depth bucket (starter / backup / other) as DK could see it when it set the week's
    salaries: the snapshot chart 6 days before the week's Sunday (2025+ files; the game-day chart if no
    snapshot is that old), else the previous week's weekly chart (week 1: its own)."""
    from dfs.projections.nfl import depth_bucket

    key = ("oracle_dk_depth", season, week)
    if key in data.cache:
        return data.cache[key]
    s = data.schedules
    days = pd.to_datetime(s.loc[(s["season"] == season) & (s["week"] == week), "gameday"])
    sunday = days[days.dt.dayofweek == 6].min() if (days.dt.dayofweek == 6).any() else days.min()
    raw = data.depth_raw.get(season)
    snapshot = raw is not None and "dt" in raw.columns and bool(raw["dt"].notna().any())
    if snapshot:
        dc = data.depth_chart(season, week, nv.kickoff_utc(str((sunday - pd.Timedelta(days=6)).date()), "08:00"))
        if dc.empty:
            dc = data.depth_chart(season, week, nv.kickoff_utc(str(sunday.date()), "08:00"))
        dc = dc.sort_values("rank").drop_duplicates(["gsis_id", "pos"])
        out = dict(zip(zip(dc["gsis_id"], dc["pos"]), depth_bucket(dc["pos"], dc["rank"])))
    else:
        out = _weekly_buckets(raw, max(week - 1, 1))
    data.cache[key] = out
    return out


# offensive alignment -> DK position; special-teams roles (KR/KOR/PR/H) and blank alignments are dropped
_WEEKLY_POS = {"QB": "QB", "RB": "RB", "HB": "RB", "FB": "FB", "WR": "WR", "LWR": "WR", "RWR": "WR", "SWR": "WR",
               "TE": "TE"}


def _weekly_buckets(raw: pd.DataFrame, week: int) -> dict[tuple[str, str], str]:
    """Weekly-format charts (2017-2024): bucket from the raw depth_team at offensive alignments only.

    ``dfs.data.nflverse.depth_chart_asof`` also counts the Special Teams formation (a kick returner
    becomes a rank-1 WR) and blank alignments, which demotes real starters (2021 week 1: Diggs and
    Justin Jefferson rank WR4, Kelce TE2); this reads the chart directly instead. Starter = depth_team 1
    (three WR alignments give three WR starters), backup = 2 (RB: 2-3), fullbacks at most backup."""
    r = raw[(raw["week"] == week) & (raw["formation"] == "Offense")]
    pos = r["depth_position"].astype(str).str.strip().str.upper().map(_WEEKLY_POS)
    rank = pd.to_numeric(r["depth_team"], errors="coerce")
    d = pd.DataFrame({"gsis_id": r["gsis_id"], "pos": pos, "rank": rank}).dropna()
    d.loc[d["pos"] == "FB", "rank"] = d.loc[d["pos"] == "FB", "rank"].clip(lower=2)
    d["pos"] = d["pos"].replace("FB", "RB")
    d = d.sort_values("rank").drop_duplicates(["gsis_id", "pos"])
    b = np.where(d["rank"] <= 1, "starter",
                 np.where((d["rank"] <= 2) | ((d["pos"] == "RB") & (d["rank"] <= 3)), "backup", "other"))
    return dict(zip(zip(d["gsis_id"], d["pos"]), b.tolist()))


def blend(f: pd.DataFrame, k: float, col: str = "ppg") -> pd.Series:
    """w * col + (1 - w) * ppg_prev with w = n / (n + k); either side alone when the other is missing."""
    w = f["n"] / (f["n"] + k)
    cur, prev = f[col], f["ppg_prev"]
    out = w * cur + (1 - w) * prev
    return out.where(cur.notna() & prev.notna(), cur.fillna(prev))


# --------------------------------------------------------------------------- model


def _design(f: pd.DataFrame, k: float, extra: list[str]) -> np.ndarray:
    b = blend(f, k)
    l3 = blend(f, k, "l3").fillna(b)
    cols = [np.ones(len(f)), b.to_numpy(float), l3.to_numpy(float)]
    for e in extra:
        if e == "starter_x_blend":
            cols.append(f["starter"].astype(float).fillna(0.0).to_numpy() * b.to_numpy(float))
        else:
            cols.append(f[e].astype(float).fillna(0.0).to_numpy())
    return np.column_stack(cols)


def _fit_pos(X: np.ndarray, y: np.ndarray, lo: float, hi: float) -> np.ndarray:
    coef0, *_ = np.linalg.lstsq(X, y, rcond=None)
    res = least_squares(lambda c: np.clip(X @ c, lo, hi) - y, coef0, loss="soft_l1", f_scale=500.0)
    return res.x


def has_history(f: pd.DataFrame) -> pd.Series:
    return f["ppg"].notna() | f["ppg_prev"].notna()


def predict(f: pd.DataFrame, model: dict, pos) -> np.ndarray:
    """DK-like salaries for rows of ``f`` (dk_features columns plus ``starter`` (0/1), ``dc``
    (starter/backup/other), ``implied`` and ``opp_implied``), aligned with ``pos``."""
    f = f.reset_index(drop=True)
    pos = np.asarray(pos, dtype=object)
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
        fb = model["no_history"]
        dc = f["dc"].fillna("other").to_numpy() if "dc" in f.columns else np.full(len(f), "other")
        out[miss] = [fb.get(f"{pos[i]}|{dc[i]}", fb.get(f"{pos[i]}|other", RANGE.get(pos[i], (3000, 0))[0]))
                     for i in miss]
    return (np.round(out / 100.0) * 100).astype(int)


# --------------------------------------------------------------------------- training data


def load_file(path: Path, kind: str) -> Contest:
    if kind == "json":
        return Contest.model_validate(json.loads(path.read_text()))
    from dfs.io.contest_file import load_contest

    return load_contest(str(path))


def training_rows(data) -> pd.DataFrame:
    """One row per real-file player matched to nflverse (name + team via dfs.projections.matching)."""
    from dfs.backtest import add_actual_points
    from dfs.projections.nfl import build_context
    from dfs.projections.nfl_provider import infer_week

    add_actual_points(data, (TABLE,))
    rows = []
    for path, kind in FILES:
        c = load_file(path, kind)
        site, gt = (str(getattr(x, "value", x)) for x in (c.rules.site, c.rules.game_type))
        assert site == "draftkings" and gt == "classic", (path, site, gt)
        first = min(p.start_time for p in c.players.values() if p.start_time)
        season, week = infer_week(c, data.schedules, nv.current_season(first.date()))
        r = data.rosters
        r = r[(r["season"] == season) & (r["week"] == week) & r["pos"].isin(["QB", "RB", "WR", "TE"])]
        cands = pd.DataFrame({"gsis_id": r["gsis_id"], "name": r["full_name"], "team": r["team"],
                              "position": r["pos"]}).dropna()
        rep = match_players(c.players, cands)
        sal = {cd.player_key: cd.salary for cd in c.candidates}
        feats = dk_features(data, season, week)
        ctx = build_context(data, season, week)
        dcb = dk_depth(data, season, week)
        imp = dict(zip(ctx.games["team"], ctx.games["implied"]))
        opp = dict(zip(ctx.games["team"], ctx.games["opp_implied"]))
        n_ok = 0
        for k, p in c.players.items():
            pos = p.positions[0]
            m = rep.matched.get(k)
            if pos not in POSITIONS or m is None:
                continue
            team = canonicalize(p.team, strict=False)
            pid = m.data_id if pos != "DST" else f"DST:{team}"
            fr = feats.loc[pid].to_dict() if pid in feats.index else {"ppg": np.nan, "n": 0, "ppg_prev": np.nan,
                                                                       "n_prev": 0, "l3": np.nan}
            dc = "starter" if pos == "DST" else dcb.get((pid, pos), "other")
            rows.append({"file": path.name, "season": season, "week": week, "player_id": pid, "name": p.name,
                         "team": team, "pos": pos, "salary": sal[k], "site_avg": p.site_avg, "dc": dc,
                         "starter": float(dc == "starter"), "implied": imp.get(team), "opp_implied": opp.get(team),
                         **fr})
            n_ok += 1
        print(f"{path.name}: {season} wk{week} DK classic, {len(c.players)} players, matched {n_ok} "
              f"(unmatched {len(rep.unmatched)})", file=sys.stderr)
    return pd.DataFrame(rows)


def _metrics(y: np.ndarray, pred: np.ndarray) -> dict:
    y, pred = np.asarray(y, float), np.asarray(pred, float)
    ss = ((y - y.mean()) ** 2).sum()
    return {"r2": round(float(1 - ((y - pred) ** 2).sum() / ss), 4) if ss > 0 else None,
            "mae": round(float(np.mean(np.abs(y - pred))), 1), "n": int(len(y))}


def _holdout(g: pd.DataFrame, f: str, k: float, extra: list[str], lo: float, hi: float) -> tuple[np.ndarray, np.ndarray]:
    tr, te = g[g["file"] != f], g[g["file"] == f]
    c = _fit_pos(_design(tr, k, extra), tr["salary"].to_numpy(float), lo, hi)
    return te["salary"].to_numpy(float), np.round(np.clip(_design(te, k, extra) @ c, lo, hi), -2)


def fit(d: pd.DataFrame) -> dict:
    files = sorted(d["file"].unique())
    model = {"table": TABLE, "files": files, "positions": {}, "no_history": {}}
    report = {}
    hist = d[has_history(d)]
    for pos in POSITIONS:
        g = hist[hist["pos"] == pos]
        lo, hi = RANGE[pos]
        extra = EXTRA[pos]
        y = g["salary"].to_numpy(float)
        cv = {}
        for k in K_GRID:  # leave-one-file-out MAE picks the season vs last-season weighting
            parts = [_holdout(g, f, k, extra, lo, hi) for f in files]
            cv[k] = float(np.mean(np.abs(np.concatenate([a - b for a, b in parts]))))
        k = min(cv, key=cv.get)
        coef = _fit_pos(_design(g, k, extra), y, lo, hi)
        model["positions"][pos] = {"k": k, "extra": extra, "coef": [round(float(x), 4) for x in coef],
                                   "coef_names": ["const", "blend", "l3_blend", *extra], "range": [lo, hi]}
        pred = predict(g, model, g["pos"]).astype(float)
        above = (y > lo)
        report[pos] = {"in_sample": _metrics(y, pred), "above_floor": _metrics(y[above], pred[above]),
                       "leave_one_file_out_mae": round(cv[k], 1), "k": k}
        for f in files:
            report[pos][f"fit_other_file_score_{f}"] = _metrics(*_holdout(g, f, k, extra, lo, hi))
    for (pos, b), g in d.groupby(["pos", "dc"]):
        model["no_history"][f"{pos}|{b}"] = float(np.round(g["salary"].median(), -2))
    report["ALL"] = _metrics(hist["salary"], predict(hist, model, hist["pos"]))
    report["rows_without_history"] = int(len(d) - len(hist))
    report["rows_by_file"] = d.groupby("file").size().to_dict()
    model["fit"] = report
    return model


if __name__ == "__main__":
    from dfs.projections.nfl import NFLData

    data = NFLData.load(range(2022, 2027), max_age_hours=1e9)
    d = training_rows(data)
    m = fit(d)
    print(json.dumps(m["fit"], indent=1))
    MODEL_PATH.write_text(json.dumps(m, indent=1))
    print(f"wrote {MODEL_PATH}", file=sys.stderr)
