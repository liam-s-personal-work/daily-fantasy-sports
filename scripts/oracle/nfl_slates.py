"""Build historical NFL DK classic MAIN slates for the oracle-gap backtest (dfs.oracle.evaluate_slate).

Main slate = the regular-season Sunday games kicking off at 1:00, 4:05 or 4:25 PM ET (nflverse
schedules weekday/gametime), with a final score; Thursday, Friday, Saturday, Sunday-night, Monday,
international 9:30 AM and other off-time games are excluded; >= 2 games. Seasons 2021-2025 weeks 1-18
and 2026 weeks 1-7 (whatever is in the nflverse cache with results: 2026 weeks 1-6 as of this build).
For 2026 weeks 3 and 6 this definition reproduces the real DK main slates (13 and 10 games).

One row per player / DST on the slate. Rows = ``dfs.backtest.evaluate_week`` pools for the main-slate
teams, DraftKings table, kickers dropped:
- pool A: every QB/RB/WR/TE who played >= 25% of offensive snaps that week, plus every DST;
- pool B: each team's top players by projection or season average (QB1, RB1-2, WR1-3, TE1) who did
  not reach the threshold or did not play (actual = their real points, often 0).

Columns
- keys: slate ("{season}-w{week:02d}"), season, week, player_id (gsis_id or DST:TEAM), name, team, opp,
  game ("AWAY@HOME", nflverse team codes, e.g. LA = Rams), pos (comma string, one of QB/RB/WR/TE/DST;
  evaluate_slate needs ``set(pos.split(','))``), pool (A/B);
- salary: DK-like pseudo-salary (scripts/oracle/nfl_salary.py: fit per position on the real 2026 week-3
  and week-6 DK files from DK-visible inputs only: DK points per game this and last season, last 3
  games, depth-chart starter flag from the chart ~6 days before the Sunday (depth_bucket_dk), and for
  DST the implied points); salary_dk: the real DK salary on the two slates where a real file exists
  (2026-w03, 2026-w06), else NaN;
- post-game (labels, never features): actual (DK points), snap_pct_actual, pool;
- engine projections (dfs.projections.nfl via evaluate_week, data strictly before the week, 4000 sims,
  seed 7): engine (= unconditional mean), engine_cond (= mean given playing / starting for QBs), p10,
  p50, p90 (played distribution), p_play;
- naive baselines (evaluate_week): base_std (season-to-date DK ppg, last season / position median
  fallback), base_last3, base_xfp (ff_opportunity expected points, NaN for DST);
- pre-game features (build_context / player_means, or history strictly before the week): home, late
  (4:05/4:25 game), implied, opp_implied, spread (team), total, roof, status (weekly roster status; INA =
  game-day inactive, known ~90 min before kickoff, i.e. after the 1 PM lock for late games), inj_report,
  inj_practice, role, depth_rank, depth_bucket (game-day chart), depth_bucket_dk, p_active, pi_start, share/stat means given playing
  (s_tgt, s_car, s_rtd, s_utd, proj_targets, proj_rec, proj_rec_yds, proj_carries, proj_rush_yds,
  proj_pass_att, proj_pass_yds, proj_pass_td, proj_rush_td, proj_rec_td), last_pct (snap share in the
  last game played), last_start, games_std / snap_std / tgt_share_std / car_share_std (season to
  date), snap_prev (last season), dk_ppg, dk_n, dk_ppg_prev, dk_n_prev, dk_l3 (the salary inputs),
  opp_pos_allowed (opponent's DK points allowed to the position per game vs league, shrunk to 1).

Leakage notes (also in the parquet key-value metadata, key b"oracle_notes")
- Pool A membership conditions on the realized >= 25% snap share. That is more than knowing inactives:
  a reserve who only played because a teammate got hurt during the game is in the pool, and a non-top
  player who was active but barely played is out. Accepted here because the live engine also learns
  inactives before lock and the busts that are left out are mostly cheap fringe players; every model
  sees the same pool, but absolute pct-of-optimal is somewhat optimistic for all of them.
- Projections and every feature column use data strictly before (season, week); the week's final
  injury report, weekly roster status and the 08:00 ET game-day depth chart are pre-game. Thursday
  games of the same week are not used (conservative). Closing spread/total are used (known at lock).
- Salaries are a model: DK's pricing is approximated (in-sample R^2 0.83-0.92 by position on 2026);
  2021-2025 prices are extrapolated from 2026 behaviour (e.g. caps above the 2026 maxima).

    python3 scripts/oracle/nfl_slates.py [out.parquet] [--only=2026-6,2025-3]   # build (~8 min, 3 processes)
    python3 scripts/oracle/nfl_slates.py [out.parquet] --eval   # pct of optimal per slate -> *_eval.parquet
"""

from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
from pathlib import Path

import numpy as np
import pandas as pd

from dfs.backtest import add_actual_points, evaluate_week
from dfs.data import nflverse as nv
from dfs.projections.nfl import NFLData, build_context, player_means

sys.path.insert(0, str(Path(__file__).parent))
import nfl_salary  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data/cache/oracle/nfl_slates.parquet"
TABLE = "draftkings.nfl"
MAIN_TIMES = {"13:00", "16:05", "16:25"}
SEASONS = {2021: 18, 2022: 18, 2023: 18, 2024: 18, 2025: 18, 2026: 7}
N_SIMS, SEED = 4000, 7
MODEL = json.loads(nfl_salary.MODEL_PATH.read_text())
DATA: NFLData | None = None  # loaded once in the parent, shared by fork


def load_data() -> NFLData:
    data = NFLData.load(range(2017, 2027), max_age_hours=1e9)  # cache only: no refresh mid-run
    add_actual_points(data, (TABLE,))
    nfl_salary._reg_game_ids(data)
    return data


def main_slates(schedules: pd.DataFrame) -> dict[tuple[int, int], pd.DataFrame]:
    """(season, week) -> main-slate games (Sunday 1:00 / 4:05 / 4:25 PM ET, regular season, scored)."""
    s = schedules[(schedules["game_type"] == "REG") & (schedules["weekday"] == "Sunday")
                  & schedules["gametime"].isin(MAIN_TIMES) & schedules["home_score"].notna()]
    out = {}
    for (season, week), g in s.groupby(["season", "week"]):
        if SEASONS.get(int(season), 0) >= int(week) and len(g) >= 2:
            out[(int(season), int(week))] = g
    return out


def _season_usage(data: NFLData, season: int, week: int) -> pd.DataFrame:
    """Season-to-date snap / target / carry shares (games played) and last season's snap share."""
    pg = nv.before(data.player_games, season, week, min_season=season - 1)
    pg = pg[pg["game_id"].isin(nfl_salary._reg_game_ids(data))]
    pg = pg[(pg["offense_pct"].fillna(0) > 0) | ((pg["targets"] + pg["carries"] + pg["attempts"]) > 0)]
    cur, prev = pg[pg["season"] == season], pg[pg["season"] == season - 1]
    g = cur.groupby("gsis_id")
    s = g[["targets", "team_targets", "carries", "team_carries"]].sum()
    out = pd.DataFrame({"games_std": g.size(), "snap_std": g["offense_pct"].mean(),
                        "tgt_share_std": s["targets"] / s["team_targets"].replace(0, np.nan),
                        "car_share_std": s["carries"] / s["team_carries"].replace(0, np.nan)})
    return out.join(prev.groupby("gsis_id")["offense_pct"].mean().rename("snap_prev"), how="outer")


def _opp_allowed(data: NFLData, season: int, week: int, k: float = 4.0) -> dict[tuple[str, str], float]:
    """(opponent, pos) -> DK points allowed per game to that position relative to the league, this season
    (last season before week 2), shrunk toward 1 with ``k`` pseudo-games. DST: points scored by DSTs
    facing that offense."""
    use = season if week > 1 else season - 1
    pg = nv.before(data.player_games, season, week, min_season=use)
    pg = pg[(pg["season"] == use) & pg["game_id"].isin(nfl_salary._reg_game_ids(data))]
    per = pg.groupby(["opponent", "pos", "game_id"])[f"fp_{TABLE}"].sum().reset_index()
    tg = nv.before(data.team_games, season, week, min_season=use)
    tg = tg[(tg["season"] == use) & tg["played"]]
    per = pd.concat([per, pd.DataFrame({"opponent": tg["opponent"], "pos": "DST", "game_id": tg["game_id"],
                                        f"fp_{TABLE}": tg[f"dst_{TABLE}"]})], ignore_index=True)
    league = per.groupby("pos")[f"fp_{TABLE}"].mean()
    agg = per.groupby(["opponent", "pos"])[f"fp_{TABLE}"].agg(["mean", "size"]).reset_index()
    agg["ratio"] = agg["mean"] / agg["pos"].map(league)
    agg["ratio"] = (agg["size"] * agg["ratio"] + k) / (agg["size"] + k)
    return {(o, p): float(r) for o, p, r in zip(agg["opponent"], agg["pos"], agg["ratio"])}


MEANS = {"s_tgt": "s_tgt", "s_car": "s_car", "s_rtd": "s_rtd", "s_utd": "s_utd", "targets": "proj_targets",
         "rec": "proj_rec", "rec_yds": "proj_rec_yds", "carries": "proj_carries", "rush_yds": "proj_rush_yds",
         "pass_att": "proj_pass_att", "pass_yds": "proj_pass_yds", "pass_td": "proj_pass_td",
         "rush_td": "proj_rush_td", "rec_td": "proj_rec_td", "status": "status", "rep": "inj_report",
         "prac": "inj_practice", "role": "role", "depth_rank": "depth_rank", "dc": "depth_bucket",
         "p_active": "p_active", "pi_start": "pi_start", "last_pct": "last_pct", "last_start": "last_start"}


def build_week(sw: tuple[int, int]) -> pd.DataFrame:
    season, week = sw
    data = DATA
    t0 = time.time()
    games = main_slates(data.schedules)[(season, week)]
    try:
        r = evaluate_week(data, season, week, tables=(TABLE,), n_sims=N_SIMS, seed=SEED, kickers=False)
    except ValueError as exc:  # e.g. missing betting lines
        print(f"  {season} wk{week:02d}: skipped ({exc})", file=sys.stderr)
        return pd.DataFrame()
    r = r[(r["table"] == TABLE) & (r["pos"] != "K")].copy()
    ctx = build_context(data, season, week)  # same (cached) pieces evaluate_week used
    tgm = ctx.games.set_index("team")
    home = {nv.canonicalize(h): nv.canonicalize(a) for a, h in zip(games["away_team"], games["home_team"])}
    label = {}
    for h, a in home.items():
        label[h] = label[a] = f"{a}@{h}"
    r = r[r["team"].isin(label)].drop_duplicates("gsis_id").reset_index(drop=True)
    m = player_means(ctx).drop_duplicates("gsis_id").set_index("gsis_id")
    d = pd.DataFrame({
        "slate": f"{season}-w{week:02d}", "season": season, "week": week, "player_id": r["gsis_id"],
        "name": r["name"], "team": r["team"], "pos": r["pos"], "pool": r["pool"], "projected": r["projected"],
        "actual": r["actual"].astype(float), "snap_pct_actual": r["offense_pct"].astype(float),
        "engine": r["mean"].astype(float), "engine_cond": r["played_mean"].astype(float),
        "p10": r["p10"].astype(float), "p50": r["p50"].astype(float), "p90": r["p90"].astype(float),
        "p_play": r["p_play"].astype(float), "base_std": r["base_std"].astype(float),
        "base_last3": r["base_last3"].astype(float), "base_xfp": r["base_xfp"].astype(float)})
    d["game"] = d["team"].map(label)
    d["opp"] = d["team"].map(tgm["opponent"])
    d["home"] = d["team"].map(tgm["is_home"]).astype(float)
    d["late"] = d["team"].map(tgm["gametime"]).isin({"16:05", "16:25"}).astype(float)
    d["implied"] = d["team"].map(tgm["implied"]).astype(float)
    d["opp_implied"] = d["team"].map(tgm["opp_implied"]).astype(float)
    d["spread"] = d["team"].map(tgm["team_spread"]).astype(float)
    d["total"] = d["team"].map(tgm["total_line"]).astype(float)
    d["roof"] = d["team"].map(tgm["roof"])
    for src, dst in MEANS.items():
        d[dst] = d["player_id"].map(m[src]) if src in m.columns else np.nan
    dst_rows = d["pos"] == "DST"
    d.loc[dst_rows, "depth_bucket"] = "starter"
    d["depth_bucket"] = d["depth_bucket"].fillna("other")
    d = d.join(_season_usage(data, season, week), on="player_id")
    feats = nfl_salary.dk_features(data, season, week).add_prefix("dk_")
    d = d.join(feats, on="player_id")
    d["dk_n"] = d["dk_n"].fillna(0).astype(int)
    d["dk_n_prev"] = d["dk_n_prev"].fillna(0).astype(int)
    allowed = _opp_allowed(data, season, week)
    d["opp_pos_allowed"] = [allowed.get((o, p), 1.0) for o, p in zip(d["opp"], d["pos"])]
    dkd = nfl_salary.dk_depth(data, season, week)  # the chart DK saw when it set prices, not game day's
    d["depth_bucket_dk"] = ["starter" if p == "DST" else dkd.get((i, p), "other") for i, p in zip(d["player_id"], d["pos"])]
    sf = pd.DataFrame({"ppg": d["dk_ppg"], "n": d["dk_n"], "ppg_prev": d["dk_ppg_prev"], "n_prev": d["dk_n_prev"],
                       "l3": d["dk_l3"], "dc": d["depth_bucket_dk"],
                       "starter": (d["depth_bucket_dk"] == "starter").astype(float),
                       "implied": d["implied"], "opp_implied": d["opp_implied"]})
    d["salary"] = nfl_salary.predict(sf, MODEL, d["pos"])
    d["salary_dk"] = np.nan
    print(f"  {season} wk{week:02d}: {len(games)} games, {len(d)} rows, {time.time() - t0:.1f}s", file=sys.stderr)
    return d


COLS = ["slate", "season", "week", "player_id", "name", "team", "opp", "game", "pos", "pool", "salary", "salary_dk",
        "actual", "snap_pct_actual", "engine", "engine_cond", "p10", "p50", "p90", "p_play", "base_std",
        "base_last3", "base_xfp", "projected", "home", "late", "implied", "opp_implied", "spread", "total", "roof",
        *MEANS.values(), "depth_bucket_dk", "games_std", "snap_std", "tgt_share_std", "car_share_std", "snap_prev", "dk_ppg", "dk_n",
        "dk_ppg_prev", "dk_n_prev", "dk_l3", "opp_pos_allowed"]

NOTES = (
    "DK classic NFL main slates (Sunday 1:00/4:05/4:25 PM ET, >=2 games), 2021-2026. Rows = dfs.backtest."
    "evaluate_week pool A (>=25% offensive snaps, every DST) + pool B (team top players by projection or "
    "season average who missed the threshold or did not play) for main-slate teams; kickers dropped. "
    "LEAKAGE: pool A membership conditions on the realized snap share (availability and in-game role), more "
    "than the inactives the live engine learns before lock; all models share the pool, but absolute "
    "pct-of-optimal is optimistic. Projections/features use data strictly before the week (closing lines, "
    "final injury report, weekly roster status, 08:00 ET depth chart). actual and snap_pct_actual are "
    "post-game labels, and so is pool (A/B is decided by the realized snaps): never use them as features. "
    "status is the weekly roster status, which includes game-day inactives (INA, ~90 min before each kickoff: "
    "before lock for 1 PM games, late-swap information for 4 PM games). salary = DK-like model "
    "(scripts/oracle/nfl_salary.py, fit on real 2026 wk3/wk6 DK files; depth-chart starter flag from the chart "
    "~6 days before the Sunday = depth_bucket_dk); salary_dk = real DK salary on 2026-w03 and 2026-w06 only. "
    "pos is a comma string: use set(pos.split(',')) for dfs.oracle.evaluate_slate.")


def add_real_salaries(d: pd.DataFrame, data: NFLData) -> pd.DataFrame:
    """salary_dk from the real DK files (2026 weeks 3 and 6); checks their game list against ours."""
    rows = nfl_salary.training_rows(data)
    for path, kind in nfl_salary.FILES:
        c = nfl_salary.load_file(path, kind)
        dk_games = {"@".join(nv.canonicalize(t, strict=False) for t in g.split("@")) for g in c.games}
        g = rows[rows["file"] == path.name]
        season, week = int(g["season"].iloc[0]), int(g["week"].iloc[0])
        m = (d["season"] == season) & (d["week"] == week)
        ours = set(d.loc[m, "game"])
        if ours != dk_games:
            print(f"WARNING {season} wk{week}: main-slate games differ from DK: ours-DK={sorted(ours - dk_games)} "
                  f"DK-ours={sorted(dk_games - ours)}", file=sys.stderr)
        sal = dict(zip(g["player_id"], g["salary"]))
        d.loc[m, "salary_dk"] = d.loc[m, "player_id"].map(sal)
        got = d.loc[m, "salary_dk"].notna().mean()
        print(f"real DK salaries {season} wk{week}: {len(dk_games)} DK games = ours: {ours == dk_games}; "
              f"{got:.1%} of {m.sum()} rows matched", file=sys.stderr)
    return d


def write(d: pd.DataFrame, out: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    t = pa.Table.from_pandas(d, preserve_index=False)
    meta = dict(t.schema.metadata or {})
    meta[b"oracle_notes"] = NOTES.encode()
    meta[b"salary_model"] = json.dumps({k: MODEL[k] for k in ("positions", "no_history", "fit")}).encode()
    out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(t.replace_schema_metadata(meta), out)


EVAL_COLS = ["engine", "base_std", "engine_cond", "base_last3", "base_xfp"]


def _eval_slate(g: pd.DataFrame) -> dict:
    from dfs.oracle import Rules, evaluate_slate

    g = g.assign(pos=g["pos"].map(lambda v: set(v.split(",")))).reset_index(drop=True)
    out = {"slate": g["slate"].iloc[0], "season": int(g["season"].iloc[0]), "week": int(g["week"].iloc[0]),
           **evaluate_slate(g, EVAL_COLS, Rules("nfl"))}
    if g["salary_dk"].notna().any():  # same slate priced by DK itself (players without a DK row dropped)
        real = g[g["salary_dk"].notna()].assign(salary=lambda x: x["salary_dk"].astype(int)).reset_index(drop=True)
        out.update({f"dk_salary|{k}": v for k, v in evaluate_slate(real, ["engine", "base_std"], Rules("nfl")).items()})
    return out


def evaluate_all(path: Path) -> pd.DataFrame:
    """dfs.oracle.evaluate_slate on every slate (pos converted to sets); writes <path stem>_eval.parquet."""
    from dfs.oracle import paired_bootstrap

    d = pd.read_parquet(path)
    with ProcessPoolExecutor(3) as ex:
        res = pd.DataFrame(list(ex.map(_eval_slate, [g for _, g in d.groupby("slate")])))
    res.to_parquet(path.with_name(path.stem + "_eval.parquet"))
    pct = [f"{c}:pct" for c in EVAL_COLS if f"{c}:pct" in res.columns]
    print(f"{len(res)} slates; mean pct of optimal:\n{res[pct].mean().round(4).to_string()}")
    print(res.groupby("season")[pct].mean().round(4).assign(n=res.groupby("season").size()).to_string())
    m, lo, hi = paired_bootstrap(res["engine:pct"], res["base_std:pct"])
    print(f"engine - base_std: {m:+.4f} [{lo:+.4f}, {hi:+.4f}] (95% slate bootstrap)")
    dk = [c for c in res.columns if c.startswith("dk_salary|") and c.endswith(":pct")]
    if dk:
        both = res.dropna(subset=dk)
        print("real-DK-salary slates:\n" + both[["slate", "engine:pct", "base_std:pct", *dk]].round(4).to_string())
    return res


def main(argv: list[str]) -> None:
    global DATA
    out = Path(next((a for a in argv if a.endswith(".parquet")), OUT))
    if "--eval" in argv:
        evaluate_all(out)
        return
    only = next((a.split("=", 1)[1] for a in argv if a.startswith("--only=")), None)
    t0 = time.time()
    DATA = load_data()
    weeks = sorted(main_slates(DATA.schedules))
    if only:
        want = {tuple(int(x) for x in s.split("-")) for s in only.split(",")}
        weeks = [w for w in weeks if w in want]
    print(f"data loaded {time.time() - t0:.0f}s; {len(weeks)} main slates", file=sys.stderr)
    with ProcessPoolExecutor(3, mp_context=get_context("fork")) as ex:
        frames = list(ex.map(build_week, weeks))
    d = pd.concat([f for f in frames if len(f)], ignore_index=True)
    d = add_real_salaries(d, DATA)
    d = d[COLS]
    write(d, out)
    print(f"{d['slate'].nunique()} slates, {len(d)} rows -> {out} ({time.time() - t0:.0f}s)", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
