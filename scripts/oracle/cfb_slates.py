"""Build historical CFB DK classic MAIN slates for the oracle-gap backtest (dfs.oracle.evaluate_slate).

Main-slate rule ("attractive", default). DK's Saturday CFB main slate is the early + afternoon
window of marquee games. Per (season, week):
1. candidates: regular-season FBS-vs-FBS games kicking off on Saturday between 12:00 and 16:00 ET
   (schedule ``start_date``), with results (completed and stat lines for both teams);
2. attractiveness = number of Power-4 teams (SEC, Big Ten, Big 12, ACC, plus Notre Dame; that
   season's conference) + number of AP-ranked teams (schedule ``home_rank``/``away_rank``, the rank
   entering the game); ties broken by the pre-game total (ESPN betting file, closing-type; missing ->
   the week's median), then earlier kickoff;
3. keep the 12 most attractive (fewer if fewer exist; a week needs >= 4).
Validated on the one real DK Saturday main slate we have (2026 week 8, data/cache/real_samples/
DKEntries_CFB_2026-10-24.csv: 12 games, kickoffs 12:00-15:30 ET, every one Power-4 vs Power-4): this
rule picks 10 of DK's 12 games. The rule first suggested for this build ("total": Saturday 12:00-20:00
ET, the 12 highest totals) picks 2 of 12 (it fills the slate with Group-of-5 shootouts and night
games); ``--rule=total`` builds that variant for robustness checks. A week with a real DK main-slate
file (REAL_FILES: 2026 week 8) uses DK's own 12 games instead of the rule (slate_source "dk_file"),
with DK's salaries (salary_dk) and DK's listed positions. Seasons 2024 and 2025 weeks 2-14, 2026 weeks
2-8 (week 8: 7 of its 12 games are missing from the 2026-10-24 pbp release and are scored from ESPN's
official box, dfs derived lines v6; box lines carry no two-point conversions).

One row per player in the slate teams' pre-game DFS pool: ``dfs.backtest_cfb.week_rows`` rows with
``pool_dfs`` true (per team QB1, RB1-2, WR/TE1-4 by usage share over the team's last two games,
chosen before kickoff; a pool player who then does not record a stat scores 0). Engine = the live CFB
provider's model (dfs.projections.cfb with CFBParams(), DEFAULT_DISPERSION, DEFAULT_PZERO, as
CFBProvider and backtest_cfb.main use), data strictly before the week, seasons 2023-2026 loaded so
2024 has its prior season.

Columns
- keys: slate ("{season}-w{week:02d}"), season, week, player_id (ESPN athlete_id), name, team, opp
  (ESPN abbreviations), game ("AWAY@HOME"), team_id, opp_id, game_id, pos (DK eligibility as a comma
  string: QB, RB, WR; ESPN tight ends are "WR,TE" because DK lists TEs as WR, so they fill WR, FLEX and
  S-FLEX slots; evaluate_slate needs ``set(pos.split(','))``), pos_model (ESPN position the projection
  uses: QB/RB/WR/TE);
- salary: DK-like pseudo-salary (scripts/oracle/cfb_salary.py, fit on the real 2026 week-8 DK files from
  DK-visible inputs only: DK points per game this and last season, last 3 games, recent-usage starter
  flag); salary inputs dk_ppg, dk_n, dk_ppg_prev, dk_n_prev, dk_l3;
- post-game labels (never features): actual (DK points; 0 with no recorded stat), played_actual;
- engine (unconditional mean = (1 - p_zero) * engine_cond), engine_cond (mean given a recorded stat),
  p10, p50, p90 (played distribution), p_zero, sd;
- naive: naive_avg (season-to-date DK points per game played), naive_last (last game's points);
- pre-game features: n_s, n_prev, fpg_prev, season_share, recent_share (shrunk), recent_share_raw,
  appeared_last, last_week, exp_opps, team_opps_pg, depth (rank in the team's QB / RB / WR+TE group by
  recent usage), starter, prior_role, base, workload, env, team_ppg, team_n, team_avg_shrunk, league_ppg,
  implied, opp_implied, spread (team; negative = favoured), total, line_source, home, neutral, kick_et
  (kickoff hour ET), p4, opp_p4, rank, opp_rank (AP rank entering the game, NaN = unranked),
  opp_pos_allowed (opponent's DK points allowed per game to the QB / RB / WR+TE group vs the league,
  season to date, shrunk to 1 with 3 pseudo-games), slate_score (the game's attractiveness).

Leakage notes (also in the parquet key-value metadata, key b"oracle_notes")
- Pool, projections and features use data strictly before (season, week); lines are ESPN closing-type
  lines (known at lock); ranks are those entering the game. pool_dfs is chosen by recent usage, never by
  the week's snaps, so unlike the NFL set it has no realized-availability leak: DNPs stay in at 0.
- actual and played_actual are labels. The slate's game list keeps only games with results (cancelled
  or missing-pbp games drop out before ranking), which is data availability, not outcome selection.
- Engine parameters (CFBParams, dispersion, p_zero, calibration scale) were fit on 2025 weeks 3-13, so
  2025 slates are in-sample for the engine; 2024 (backwards) and 2026 are out of sample.
- Positions come from the ESPN season roster file (a season snapshot; usage-inferred positions are
  recomputed point in time).
- Salaries are a model of DK pricing fit on one week (2026 week 8); early-season (week 2) and 2024-2025
  prices are extrapolations, and 2026-w08's pseudo-salaries are in-sample (its salary_dk is DK's own).

    python3 scripts/oracle/cfb_slates.py [out.parquet] [--rule=total] [--only=2025-8,2024-3]  # build (~1 min)
    python3 scripts/oracle/cfb_slates.py [out.parquet] --eval [--cols=engine,naive_avg] [--out=res.csv]
        # all slates via scripts/oracle/evaluate.py run/summary with 2 processes (~1.5 min), a 3-slate
        # dfs.oracle.evaluate_slate smoke test, real-DK-salary results on 2026-w08, and (with --out) an
        # independent exact re-solve of every lineup with count_lineup (--check-only skips the evaluation)
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from dfs.backtest_cfb import week_rows
from dfs.data import cfb as cfbdata
from dfs.projections import cfb as m

sys.path.insert(0, str(Path(__file__).parent))
import cfb_salary  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data/cache/oracle/cfb_slates.parquet"
REF_DIR = ROOT / "data/cache/oracle/cfb_ref"  # 2026 schedule + lines downloaded 2026-10-26 (fallback for the week-8 check)
TABLE = "draftkings.cfb"
SEASONS = {2024: range(2, 15), 2025: range(2, 15), 2026: range(2, 9)}
LOAD_SEASONS = [2023, 2024, 2025, 2026]
P4 = {"SEC", "Big Ten", "Big 12", "ACC"}
P4_INDEPENDENT_IDS = {87}  # Notre Dame
RULES = {"attractive": {"window": ("12:00", "16:00"), "key": "attractive"},
         "total": {"window": ("12:00", "20:00"), "key": "total"}}
N_GAMES, MIN_GAMES = 12, 4
PARAMS, DISP, PZ = m.CFBParams(), m.DEFAULT_DISPERSION, m.DEFAULT_PZERO
SAL_MODEL = json.loads(cfb_salary.MODEL_PATH.read_text())
POOL_CAPS = {"QB": 1, "RB": 2, "WR": 4, "TE": 4}
REAL_FILES = {(2026, 8): cfb_salary.FILES[1]}  # real DK Saturday main slate (DK's own game list + salaries)


def load_data() -> m.CFBData:
    data = m.CFBData.load(LOAD_SEASONS, max_age_hours=1e9)  # cache only (missing files are downloaded once)
    data.points(TABLE)
    return data


# --------------------------------------------------------------------------- main-slate rule


def _games_frame(schedules: pd.DataFrame, game_lines: pd.DataFrame) -> pd.DataFrame:
    s = schedules[schedules["season_type"].astype(str).str.lower().eq("regular")].dropna(subset=["home_id", "away_id"]).copy()
    s["home_id"] = s["home_id"].astype("int64")
    s["away_id"] = s["away_id"].astype("int64")
    s["kick"] = pd.to_datetime(s["start_date"], utc=True, errors="coerce").dt.tz_convert("America/New_York")
    s = s.merge(game_lines[["game_id", "total"]].drop_duplicates("game_id"), on="game_id", how="left")
    p4 = lambda conf, tid: conf.isin(P4) | tid.isin(P4_INDEPENDENT_IDS)
    s["home_p4"] = p4(s["home_conference"], s["home_id"]).astype(int)
    s["away_p4"] = p4(s["away_conference"], s["away_id"]).astype(int)
    s["n_ranked"] = s["home_rank"].between(1, 25).astype(int) + s["away_rank"].between(1, 25).astype(int)
    s["slate_score"] = s["home_p4"] + s["away_p4"] + s["n_ranked"]
    s["fbs"] = s["fbs_game"].fillna(False).astype(bool) if "fbs_game" in s.columns else True
    return s


def pick_games(g: pd.DataFrame, rule: str = "attractive") -> pd.DataFrame:
    """The main slate among one week's scheduled games ``g`` (a _games_frame slice)."""
    lo, hi = RULES[rule]["window"]
    hm = g["kick"].dt.strftime("%H:%M")
    c = g[(g["kick"].dt.dayofweek == 5) & g["fbs"] & (hm >= lo) & (hm <= hi)].copy()
    if c.empty:
        return c
    c["total_rank"] = c["total"].fillna(c["total"].median() if c["total"].notna().any() else 50.0)
    if RULES[rule]["key"] == "attractive":
        c = c.sort_values(["slate_score", "total_rank", "kick", "game_id"], ascending=[False, False, True, True])
    else:
        c = c.sort_values(["total_rank", "kick", "game_id"], ascending=[False, True, True])
    return c.head(N_GAMES)


def _with_results(data: m.CFBData) -> pd.DataFrame:
    """Scheduled games (_games_frame) that are completed and have stat lines for both teams."""
    s = _games_frame(data.schedules, data.game_lines)
    has_lines = set(zip(data.lines["game_id"], data.lines["team_id"]))
    both = np.array([((g, h) in has_lines) and ((g, a) in has_lines)
                     for g, h, a in zip(s["game_id"], s["home_id"], s["away_id"])], dtype=bool)
    return s[s["completed"].fillna(False).astype(bool).to_numpy() & both]


def real_slate(data: m.CFBData, season: int, week: int):
    """(game ids, contest) of the real DK main-slate file for (season, week), matched to ESPN games."""
    from dfs.data.cfb_teams import match_slate
    from dfs.io.contest_file import load_contest
    from dfs.projections.cfb_provider import CFBProvider

    c = load_contest(str(REAL_FILES[(season, week)]))
    rep = match_slate(c.games, data.teams, data.schedules, season=season, week=week, game_times=CFBProvider._game_times(c))
    return {int(v) for v in rep.games.values()}, c


def main_slates(data: m.CFBData, rule: str = "attractive") -> dict[tuple[int, int], pd.DataFrame]:
    """(season, week) -> main-slate games with results (see module docstring). Weeks with a real DK main-slate
    file use DK's own game list (slate_source "dk_file"), every other week the rule (slate_source "rule")."""
    s = _with_results(data)
    out = {}
    for (season, week), g in s.groupby(["season", "week"]):
        season, week = int(season), int(week)
        if week not in SEASONS.get(season, ()):
            continue
        if (season, week) in REAL_FILES:
            ids, _ = real_slate(data, season, week)
            p = g[g["game_id"].isin(ids)].assign(slate_source="dk_file")
        else:
            p = pick_games(g, rule).assign(slate_source="rule")
        if len(p) >= MIN_GAMES:
            out[(season, week)] = p
    return out


def validate_rule(data: m.CFBData) -> dict:
    """Each rule's pick for 2026 week 8 among all scheduled games (no results needed, as live) vs the real DK
    Saturday main slate (DKEntries_CFB_2026-10-24.csv). Lines: the cache, else the refreshed copy in REF_DIR."""
    ids, c = real_slate(data, 2026, 8)
    lines = data.game_lines
    g = _games_frame(data.schedules, lines)
    g = g[(g["season"] == 2026) & (g["week"] == 8)]
    if g["total"].notna().sum() == 0 and (REF_DIR / "betting_2026.parquet").exists():
        g = _games_frame(data.schedules, cfbdata.game_lines(pd.read_parquet(REF_DIR / "betting_2026.parquet")))
        g = g[(g["season"] == 2026) & (g["week"] == 8)]
    out = {"dk_file": REAL_FILES[(2026, 8)].name, "dk_games": len(c.games), "dk_matched": len(ids)}
    label = lambda r: f"{r.away_abbreviation}@{r.home_abbreviation}"
    for rule in RULES:
        p = pick_games(g, rule)
        ours = set(p["game_id"].astype(int))
        out[rule] = {"picked": len(ours), "overlap_with_dk": len(ours & ids),
                     "ours_not_dk": sorted(label(r) for r in p.itertuples() if r.game_id not in ids),
                     "dk_not_ours": sorted(label(r) for r in g.itertuples() if r.game_id in ids - ours)}
    return out


def real_salaries(data: m.CFBData) -> dict[tuple[int, int], pd.DataFrame]:
    """(season, week) -> real DK rows (athlete_id, salary, DK position) of that week's DK main-slate file."""
    out = {}
    for sw, path in REAL_FILES.items():
        r = cfb_salary.training_rows(data, files=[path])
        r = r[r["athlete_id"].notna()].drop_duplicates("athlete_id")
        out[sw] = pd.DataFrame({"athlete_id": r["athlete_id"].astype("int64"), "salary_dk": r["salary"],
                                "pos_dk": r["pos"]})
    return out


# --------------------------------------------------------------------------- per-week rows


def _abbr(schedules: pd.DataFrame) -> dict[tuple[int, int], str]:
    """(season, team_id) -> ESPN abbreviation."""
    s = schedules.dropna(subset=["home_id", "away_id"])
    a = pd.concat([pd.DataFrame({"season": s["season"], "tid": s["home_id"], "ab": s["home_abbreviation"]}),
                   pd.DataFrame({"season": s["season"], "tid": s["away_id"], "ab": s["away_abbreviation"]})])
    a = a.dropna().drop_duplicates(["season", "tid"])
    return {(int(x), int(t)): str(b) for x, t, b in zip(a["season"], a["tid"], a["ab"])}


def opp_pos_allowed(data: m.CFBData, season: int, week: int, k: float = 3.0) -> dict[tuple[int, str], float]:
    """(opponent team_id, group QB/RB/WRTE) -> DK points allowed per game to that group relative to the
    league, this season strictly before the week, shrunk toward 1 with ``k`` pseudo-games."""
    L = cfbdata.before(data.lines.assign(fp=data.points(TABLE)), season, week)
    L = L[L["season"] == season]
    if L.empty:
        return {}
    L = L.assign(pos=m.point_in_time_pos(L))
    tg = data.team_games[["game_id", "team_id", "opp_id"]].drop_duplicates(["game_id", "team_id"])
    L = L.merge(tg, on=["game_id", "team_id"], how="inner")
    L["grp"] = np.where(L["pos"].isin(["WR", "TE"]), "WRTE", L["pos"])
    per = L.groupby(["opp_id", "grp", "game_id"])["fp"].sum().reset_index()
    league = per.groupby("grp")["fp"].mean()
    agg = per.groupby(["opp_id", "grp"])["fp"].agg(["mean", "size"]).reset_index()
    ratio = agg["mean"] / agg["grp"].map(league)
    agg["ratio"] = (agg["size"] * ratio + k) / (agg["size"] + k)
    return {(int(o), g): float(r) for o, g, r in zip(agg["opp_id"], agg["grp"], agg["ratio"])}


def build_week(data: m.CFBData, season: int, week: int, games: pd.DataFrame, abbr: dict,
               real: pd.DataFrame | None = None) -> pd.DataFrame:
    t0 = time.time()
    team_ids = set(games["home_id"].astype(int)) | set(games["away_id"].astype(int))
    feats = m.features(data, season, week, TABLE, PARAMS, team_ids=team_ids)
    rows, _ = week_rows(data, season, week, TABLE, PARAMS, DISP, PZ, feats=feats)
    r = rows[rows["pool_dfs"] & rows["team_id"].isin(team_ids) & rows["game_id"].isin(set(games["game_id"]))]
    r = r.drop_duplicates("athlete_id").reset_index(drop=True)
    gi = games.set_index("game_id")
    ab = lambda t: abbr.get((season, int(t)), str(int(t)))
    label = {int(g): f"{ab(a)}@{ab(h)}" for g, a, h in zip(games["game_id"], games["away_id"], games["home_id"])}
    home = r["is_home"].astype(int) == 1
    spread = np.where(home, r["home_spread"], -r["home_spread"]).astype(float)
    d = pd.DataFrame({
        "slate": f"{season}-w{week:02d}", "season": season, "week": week, "player_id": r["athlete_id"].astype("int64"),
        "name": r["name"], "team": r["team_id"].map(ab), "opp": r["opp_id"].map(ab),
        "game": r["game_id"].map(label), "team_id": r["team_id"].astype("int64"), "opp_id": r["opp_id"].astype("int64"),
        "game_id": r["game_id"].astype("int64"),
        "pos": r["pos"].map({"QB": "QB", "RB": "RB", "WR": "WR", "TE": "WR,TE"}), "pos_model": r["pos"],
        "actual": r["actual"].astype(float), "played_actual": r["played"].astype(bool),
        "engine": r["mean"].astype(float), "engine_cond": r["mean_played"].astype(float),
        "p10": r["p10"].astype(float), "p50": r["p50"].astype(float), "p90": r["p90"].astype(float),
        "p_zero": r["p_zero"].astype(float), "sd": r["sd"].astype(float),
        "naive_avg": r["naive_avg"].astype(float), "naive_last": r["naive_last"].astype(float),
        "n_s": r["n_s"].astype(int), "n_prev": r["n_prev"].astype(int), "fpg_prev": r["fpg_prev"].astype(float),
        "season_share": r["season_share"], "recent_share": r["recent_share"], "recent_share_raw": r["recent_share_raw"],
        "appeared_last": r["appeared_last"].astype(bool), "last_week": r["last_week"].astype(int),
        "exp_opps": r["exp_opps"], "team_opps_pg": r["team_opps_pg"], "depth": r["depth"].astype(int),
        "starter": (r["depth"] == 1).astype(float), "prior_role": r["prior_role"], "base": r["base"],
        "workload": r["workload"], "env": r["env"], "team_ppg": r["team_ppg"], "team_n": r["team_n"].astype(int),
        "team_avg_shrunk": r["team_avg_shrunk"], "league_ppg": r["league_ppg"],
        "implied": r["implied"].astype(float), "opp_implied": (r["total"] - r["implied"]).astype(float),
        "spread": spread, "total": r["total"].astype(float), "line_source": r["line_source"],
        "home": home.astype(float), "neutral": r["neutral_site"].fillna(False).astype(float)})
    kick = pd.to_datetime(d["game_id"].map(gi["start_date"]), utc=True).dt.tz_convert("America/New_York")
    d["kick_et"] = kick.dt.hour + kick.dt.minute / 60.0
    hp, ap = d["game_id"].map(gi["home_p4"]), d["game_id"].map(gi["away_p4"])
    hr, ar = d["game_id"].map(gi["home_rank"]).astype(float), d["game_id"].map(gi["away_rank"]).astype(float)
    d["p4"] = np.where(home, hp, ap).astype(float)
    d["opp_p4"] = np.where(home, ap, hp).astype(float)
    d["rank"] = np.where(home, hr, ar)
    d["opp_rank"] = np.where(home, ar, hr)
    d["slate_score"] = d["game_id"].map(gi["slate_score"]).astype(float)
    allowed = opp_pos_allowed(data, season, week)
    grp = np.where(d["pos_model"].isin(["WR", "TE"]), "WRTE", d["pos_model"])
    d["opp_pos_allowed"] = [allowed.get((int(o), g), 1.0) for o, g in zip(d["opp_id"], grp)]
    # DK-like salary from DK-visible inputs only
    f = cfb_salary.dk_features(data, season, week).reindex(d["player_id"].to_numpy())
    f = f.reset_index(drop=True)
    f["n"] = f["n"].fillna(0).astype(int)
    f["n_prev"] = f["n_prev"].fillna(0).astype(int)
    f["starter"] = d["starter"].to_numpy()
    f["implied"] = d["implied"].to_numpy()
    d["salary"] = cfb_salary.predict(f, SAL_MODEL, d["pos_model"])
    for c in ("ppg", "n", "ppg_prev", "n_prev", "l3"):
        d[f"dk_{c}"] = f[c].to_numpy()
    d["slate_source"] = str(games["slate_source"].iloc[0])
    d["salary_dk"], d["pos_dk"] = np.nan, None
    if real is not None:  # the real DK file: its salaries, and its listed position as the eligibility
        rs = real.set_index("athlete_id")
        d["salary_dk"] = d["player_id"].map(rs["salary_dk"]).astype(float)
        d["pos_dk"] = d["player_id"].map(rs["pos_dk"])
        has = d["pos_dk"].notna()
        te = has & (d["pos_model"] == "TE") & (d["pos_dk"] == "WR")
        d.loc[has, "pos"] = d.loc[has, "pos_dk"]
        d.loc[te, "pos"] = "WR,TE"
    print(f"  {season} wk{week:02d}: {len(games)} games, {len(d)} rows, {time.time() - t0:.1f}s", file=sys.stderr)
    return d


COLS = ["slate", "season", "week", "player_id", "name", "team", "opp", "game", "team_id", "opp_id", "game_id", "pos",
        "pos_model", "salary", "actual", "played_actual", "engine", "engine_cond", "p10", "p50", "p90", "p_zero", "sd",
        "naive_avg", "naive_last", "dk_ppg", "dk_n", "dk_ppg_prev", "dk_n_prev", "dk_l3", "n_s", "n_prev", "fpg_prev",
        "season_share", "recent_share", "recent_share_raw", "appeared_last", "last_week", "exp_opps", "team_opps_pg",
        "depth", "starter", "prior_role", "base", "workload", "env", "team_ppg", "team_n", "team_avg_shrunk",
        "league_ppg", "implied", "opp_implied", "spread", "total", "line_source", "home", "neutral", "kick_et", "p4",
        "opp_p4", "rank", "opp_rank", "opp_pos_allowed", "slate_score", "slate_source", "salary_dk", "pos_dk"]

NOTES = (
    "DK classic CFB main slates, 2024-2025 weeks 2-14 and 2026 weeks 2-8. 2026-w08 (slate_source 'dk_file') is the real "
    "DK Saturday main slate of 2026-10-24: DK's 12 games, salary_dk = DK's salaries and pos_dk = DK's listed position "
    "(pseudo-salary there is in-sample for the salary model; 7 of its 12 games are scored from ESPN's official box "
    "because the pbp release lags: no two-point conversions). Other weeks (slate_source 'rule'), rule '{rule}': Saturday FBS-vs-FBS "
    "games with results kicking off {lo}-{hi} ET; 'attractive' keeps the 12 with the most Power-4 (SEC/Big Ten/Big 12/"
    "ACC/Notre Dame) plus AP-ranked teams, ties by pre-game total (picks 10 of the 12 games of the real DK main slate of "
    "2026-10-24); 'total' keeps the 12 highest totals (2 of 12). Rows = dfs.backtest_cfb.week_rows pool_dfs (per team "
    "QB1, RB1-2, WR/TE1-4 by usage over the team's last two games, chosen pre-game; non-players score 0). "
    "Engine = live CFB provider model (CFBParams/DEFAULT_DISPERSION/DEFAULT_PZERO, fit on 2025 weeks 3-13: 2025 slates "
    "are in-sample for the engine). Projections/features use data strictly before the week; lines are ESPN closing-type; "
    "ranks are entering the game. actual and played_actual are post-game labels: never features. pos is DK eligibility "
    "(TE -> 'WR,TE' because DK lists TEs as WR); use set(pos.split(',')) for dfs.oracle.evaluate_slate with Rules('cfb'). "
    "salary = DK-like model (scripts/oracle/cfb_salary.py, fit on the real 2026 week-8 DK files; DK points per game this/"
    "last season, last 3 games, recent-usage starter flag); early-season and 2024-2025 prices are extrapolations.")


def write(d: pd.DataFrame, out: Path, rule: str, validation: dict) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    t = pa.Table.from_pandas(d, preserve_index=False)
    meta = dict(t.schema.metadata or {})
    lo, hi = RULES[rule]["window"]
    meta[b"oracle_notes"] = NOTES.format(rule=rule, lo=lo, hi=hi).encode()
    meta[b"slate_rule_validation"] = json.dumps(validation).encode()
    meta[b"salary_model"] = json.dumps({k: SAL_MODEL[k] for k in ("positions", "no_history", "ranges")}).encode()
    out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(t.replace_schema_metadata(meta), out)


# --------------------------------------------------------------------------- evaluation


def count_lineup(pos: list[set], salary: np.ndarray, game: np.ndarray, score: np.ndarray, cap: int = 50000,
                 min_games: int = 2) -> list[int] | None:
    """Independent exact check of dfs.oracle.best_lineup for DK CFB classic. With single DK positions (QB, RB, WR;
    TE listed as WR) the slot assignment QB, RB, RB, WR, WR, WR, FLEX(RB/WR/TE), S-FLEX(QB/RB/WR/TE) is feasible
    iff 8 players, 1-2 QBs, >= 2 RBs, >= 3 WR-eligible (Hall's condition; a TE-only player is an overflow), so the
    lineup is a symmetry-free count MILP over players."""
    from scipy.optimize import Bounds, LinearConstraint, milp

    n = len(pos)
    kinds = [frozenset(p) for p in pos]
    if any(k not in {frozenset({"QB"}), frozenset({"RB"}), frozenset({"WR"}), frozenset({"WR", "TE"}), frozenset({"TE"})}
           for k in kinds):
        return None
    games = sorted(set(game))
    gi = {g: j for j, g in enumerate(games)}
    nv = n + len(games)
    c = np.concatenate([-np.asarray(score, float), np.zeros(len(games))])
    A, lo, hi = [], [], []
    row = lambda v, a, b: (A.append(v), lo.append(a), hi.append(b))
    q = np.array([1.0 if "QB" in k else 0.0 for k in kinds] + [0.0] * len(games))
    r = np.array([1.0 if "RB" in k else 0.0 for k in kinds] + [0.0] * len(games))
    w = np.array([1.0 if "WR" in k else 0.0 for k in kinds] + [0.0] * len(games))
    row(np.concatenate([np.ones(n), np.zeros(len(games))]), 8, 8)
    row(q, 1, 2)
    row(r, 2, np.inf)
    row(w, 3, np.inf)
    row(np.concatenate([np.asarray(salary, float), np.zeros(len(games))]), -np.inf, cap)
    for g, j in gi.items():
        v = np.zeros(nv)
        v[:n] = -(np.asarray(game) == g).astype(float)
        v[n + j] = 1
        row(v, -np.inf, 0)
    row(np.concatenate([np.zeros(n), np.ones(len(games))]), min_games, np.inf)
    res = milp(c, constraints=LinearConstraint(np.array(A), lo, hi), integrality=np.ones(nv), bounds=Bounds(0, 1),
               options={"disp": False})
    if res.x is None:
        return None
    return sorted(int(i) for i in np.where(res.x[:n] > 0.5)[0])


def exact_check(path: Path, res_csv: Path, cols: list[str]) -> pd.DataFrame:
    """Re-solve every slate's ex-post optimum and each column's lineup with ``count_lineup`` and compare with the
    dfs.oracle results in ``res_csv`` (evaluate.py --out)."""
    d = pd.read_parquet(path)
    res = pd.read_csv(res_csv).set_index("slate")
    out = []
    for sid, g in d.groupby("slate"):
        g = g.reset_index(drop=True)
        pos = [set(s.split(",")) for s in g["pos"]]
        act = g["actual"].to_numpy(float)
        o = count_lineup(pos, g["salary"].to_numpy(), g["game"].to_numpy(), act)
        row = {"slate": sid, "optimal_check": float(act[o].sum()), "optimal_oracle": float(res.loc[sid, "optimal"])}
        for c in cols:
            sc = g[c].fillna(0.0).to_numpy(float)
            lu = count_lineup(pos, g["salary"].to_numpy(), g["game"].to_numpy(), sc)
            row[f"{c}:proj_check"] = float(sc[lu].sum())
            row[f"{c}:proj_oracle"] = float(res.loc[sid, f"{c}:proj"])
            row[f"{c}:pct_check"] = float(act[lu].sum()) / row["optimal_check"]
        out.append(row)
    chk = pd.DataFrame(out)
    gap = chk["optimal_check"] - chk["optimal_oracle"]
    print(f"exact check over {len(chk)} slates: ex-post optimum oracle vs count-MILP max |diff| {gap.abs().max():.4f}; "
          + ", ".join(f"{c} projected-sum max |diff| {(chk[f'{c}:proj_check'] - chk[f'{c}:proj_oracle']).abs().max():.4f}"
                      for c in cols), file=sys.stderr)
    return chk


def evaluate(path: Path, cols: list[str], workers: int = 2, out_csv: Path | None = None) -> pd.DataFrame:
    """Every slate through scripts/oracle/evaluate.py's run/summary with ``workers`` processes, then a smoke test
    (dfs.oracle.evaluate_slate on 3 slates in this process) and, on slates with a real DK file, the same columns
    priced with DK's own salaries. The pool runs first: HiGHS must not have run in the parent before it forks
    (forked workers then hang spinning)."""
    from dfs.oracle import Rules, evaluate_slate, paired_bootstrap
    import evaluate as ev

    d = pd.read_parquet(path)
    res = ev.run(d, cols, "cfb", workers=workers)
    if out_csv:
        res.to_csv(out_csv, index=False)
    print(ev.summary(res, cols))
    for s, g in res.groupby("season"):
        print(f"\nseason {s}: " + ", ".join(f"{c} {100 * g[f'{c}:pct'].mean():.2f}%" for c in cols) + f" (n={len(g)})")
        for c in cols[1:]:
            if len(g) > 2:
                mm, lo, hi = paired_bootstrap(100 * g[f"{c}:pct"], 100 * g[f"{cols[0]}:pct"])
                print(f"  {c} - {cols[0]}: {mm:+.2f} [{lo:+.2f}, {hi:+.2f}]")
    ids = sorted(d["slate"].unique())
    for sid in [ids[0], ids[len(ids) // 2], ids[-1]]:
        g = d[d["slate"] == sid].reset_index(drop=True).copy()
        g["pos"] = g["pos"].map(lambda s: set(s.split(",")))
        r = evaluate_slate(g, cols, Rules("cfb"))
        print(f"smoke {sid}: {len(g)} players, optimal {r['optimal']:.1f}, "
              + ", ".join(f"{c} {r[f'{c}:pts']:.1f} ({100 * r[f'{c}:pct']:.1f}%)" for c in cols))
    for sid, g in d[d["salary_dk"].notna()].groupby("slate"):
        g = g.assign(salary=g["salary_dk"].astype(int), pos=g["pos"].map(lambda s: set(s.split(",")))).reset_index(drop=True)
        r = evaluate_slate(g, cols, Rules("cfb"))
        p = res.set_index("slate").loc[sid]
        print(f"real DK salaries {sid} ({len(g)} pool players priced by DK): optimal {r['optimal']:.1f} (pseudo "
              f"{p['optimal']:.1f}); " + ", ".join(f"{c} {100 * r[f'{c}:pct']:.1f}% (pseudo {100 * p[f'{c}:pct']:.1f}%)"
                                                   for c in cols))
    return res


def main(argv: list[str]) -> None:
    out = Path(next((a for a in argv if a.endswith(".parquet")), OUT))
    rule = next((a.split("=", 1)[1] for a in argv if a.startswith("--rule=")), "attractive")
    if "--eval" in argv:
        cols = next((a.split("=", 1)[1].split(",") for a in argv if a.startswith("--cols=")), ["engine", "naive_avg"])
        csv = next((Path(a.split("=", 1)[1]) for a in argv if a.startswith("--out=")), None)
        if "--check-only" not in argv:
            evaluate(out, cols, out_csv=csv)
        if csv is not None:
            chk = exact_check(out, csv, cols)
            print("count-MILP check, mean % of optimal: " + ", ".join(
                f"{c} {100 * chk[f'{c}:pct_check'].mean():.2f}%" for c in cols))
        return
    only = next((a.split("=", 1)[1] for a in argv if a.startswith("--only=")), None)
    t0 = time.time()
    data = load_data()
    slates = main_slates(data, rule)
    if only:
        want = {tuple(int(x) for x in s.split("-")) for s in only.split(",")}
        slates = {k: v for k, v in slates.items() if k in want}
    missing = [(s, w) for s, ws in SEASONS.items() for w in ws if (s, w) not in slates]
    print(f"data loaded {time.time() - t0:.0f}s; rule {rule}: {len(slates)} main slates; no slate with results: {missing}",
          file=sys.stderr)
    validation = validate_rule(data)
    print(f"slate rule vs real DK main slate 2026-10-24: {json.dumps(validation)}", file=sys.stderr)
    abbr = _abbr(data.schedules)
    real = real_salaries(data)
    frames = [build_week(data, s, w, g, abbr, real.get((s, w))) for (s, w), g in sorted(slates.items())]
    d = pd.concat([f for f in frames if len(f)], ignore_index=True)[COLS]
    write(d, out, rule, validation)
    print(f"{d['slate'].nunique()} slates, {len(d)} rows -> {out} ({time.time() - t0:.0f}s)", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
