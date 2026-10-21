"""College-football statistical baseline: season averages adjusted for recent workload.

For a target (season, week), using only games strictly before that week:

1. Base rate. Season-to-date fantasy points per game played (scored with ``dfs.scoring`` for the
   contest's table), shrunk toward (a) last season's points per game (each prior game counts
   ``w_prev`` of a current one) and (b) a role prior: the position's points per unit of usage
   share times the player's season share (``k0`` pseudo games).
2. Workload. The player's share of team opportunities (QB: dropbacks; RB: carries + targets;
   WR/TE: targets, against team pass attempts), counted on non-garbage-time snaps
   (``usage_basis``), over the last ``recent_games`` team games he played, shrunk toward his
   season share with ``k_recent`` pseudo team-opportunities. Points scale by
   (recent / season share) ** ``alpha`` when the share rose and ** ``alpha_down`` when it fell
   (falls are noisier), clipped.
3. Game environment, when a line exists: (team implied points / team season scoring average,
   shrunk toward the league) ** ``beta`` (clipped). A per-position ``scale`` calibrates the mean.
4. Availability. Players with no recorded offensive play in the team's last ``recent_games``
   games are removed ("no recent role"). ``p_zero`` (no recorded stat in the game) is a
   per-position logistic on: missed the team's latest game (a role that vanished last week),
   expected opportunities, and depth rank in the team's position group.
5. Spread. Played-outcome SD = ``a + b * mean`` by position (fit on residuals of past
   projections), turned into p10/p50/p90 with ``quantiles_from_mean_sd`` and a fitted skew.
   ``mean`` = (1 - p_zero) * played mean.

Defaults (``CFBParams``, ``DEFAULT_DISPERSION``, ``DEFAULT_PZERO``) were fit on 2025 weeks 3-13
with 2024 as the prior season (``python -m dfs.backtest_cfb --fit``); 2026 is out of sample.
Results: ``docs/results/cfb_baseline_backtest.md``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from typing import Iterable, Mapping

import numpy as np
import pandas as pd

from dfs.data import cfb as cfbdata
from dfs.data import cfb_stats as cs
from dfs.distributions import quantiles_from_mean_sd

POSITIONS = ("QB", "RB", "WR", "TE")
# NCAA/ESPN official statistics count sacks as rushing plays (QB rushing yards include sack
# losses) and DK/FD score from official stats; set False to score rushing without sack yards.
SACKS_IN_RUSHING = True
ROSTER_POS = {"QB": "QB", "RB": "RB", "FB": "RB", "HB": "RB", "TB": "RB", "WR": "WR", "SB": "WR", "TE": "TE"}
DERIVED_VERSION = "cfb-lines-v5"  # v5: postseason weeks remapped, ESPN team rows out of the box, two-point fixes


# --------------------------------------------------------------------------- parameters


@dataclass(frozen=True)
class CFBParams:
    """Defaults fit on 2025 weeks 3-13 (prior season 2024) by ``python -m dfs.backtest_cfb --fit``."""

    recent_games: int = 2
    k_recent: float = 40.0  # pseudo team-opportunities pulling the recent share to the season share
    alpha: float = 0.5  # elasticity of points to the workload ratio when the recent share is up
    alpha_down: float = 0.1  # ... and when it is down (down-moves are noisier: blowouts, one-off game plans)
    ratio_lo: float = 0.3
    ratio_hi: float = 2.5
    k0: float = 2.0  # pseudo games of the role prior
    w_prev: float = 0.2  # weight of one prior-season game relative to a current-season game
    cap_prev: int = 12
    beta: float = 0.5  # elasticity to implied points vs the team's (shrunk) season scoring
    k_team: float = 3.0  # pseudo games pulling a team's scoring average to the league's
    env_lo: float = 0.6
    env_hi: float = 1.6
    # "played": recent share over the team's last games the player appeared in (availability is
    # p_zero's job); "team": over all of the team's last games (absences count as zero share)
    recent_mode: str = "played"
    # "competitive": shares from non-garbage-time snaps only (CFB blowouts distort shares); "all": every snap
    usage_basis: str = "competitive"
    # per-position multiplier on the played mean (sum actual / sum projected on the fit season's dfs pool)
    scale: Mapping[str, float] = field(default_factory=lambda: {"QB": 1.0475, "RB": 1.0104, "WR": 0.9748, "TE": 0.9524})

    def with_(self, **kw) -> "CFBParams":
        return replace(self, **kw)


# Played-outcome SD = a + b * mean_played, and the two-piece skew, by position (fit on 2025 residuals).
DEFAULT_DISPERSION: dict[str, dict[str, float]] = {
    "QB": {"a": 6.212, "b": 0.219, "skew": 0.3},
    "RB": {"a": 3.626, "b": 0.424, "skew": 0.6},
    "WR": {"a": 2.623, "b": 0.527, "skew": 0.6},
    "TE": {"a": 1.884, "b": 0.557, "skew": 0.6},
}
# P(no recorded stat in the game): logistic by position on PZ_FEATURES, where x = log1p(expected
# opportunities this game), missed = no recorded play in the team's latest game, and depth ranks the
# player within his team's QB / RB / WR+TE group by recent usage share (all pre-game). Fit on 2025.
PZ_FEATURES = ("intercept", "missed_last", "x", "depth1", "depth2", "x^2", "missed_last*x")
DEFAULT_PZERO: dict[str, dict[str, list[float]]] = {
    "QB": {"coef": [0.7885, 0.5093, -0.2806, -1.4043, -0.5787, -0.0788, 0.5457]},
    "RB": {"coef": [1.1585, 0.2542, -1.6847, 0.1219, -0.167, 0.0506, 1.0779]},
    "WR": {"coef": [1.363, 0.2843, -2.4945, -0.6802, -0.5097, 0.2324, 0.9409]},
    "TE": {"coef": [1.2204, 0.179, -1.8294, 0.0869, -0.1136, -0.2082, 0.601]},
}


# --------------------------------------------------------------------------- data bundle


@dataclass
class CFBData:
    """Everything the CFB model reads, loaded once; projections filter it point-in-time."""

    seasons: list[int]
    lines: pd.DataFrame  # player-game stat lines (pbp, repaired with box where pbp incomplete)
    team_totals: pd.DataFrame  # per team-game usage totals (+ usage_ok)
    team_games: pd.DataFrame  # one row per team per scheduled game (+ points, implied when a line exists)
    game_lines: pd.DataFrame  # game_id, home_spread, total, line_source
    positions: pd.DataFrame  # season, athlete_id, pos
    rosters: pd.DataFrame
    teams: pd.DataFrame
    schedules: pd.DataFrame
    validation: dict = field(default_factory=dict)
    raw_lines: pd.DataFrame = field(default_factory=pd.DataFrame, repr=False)  # pbp lines before the box repair
    cache: dict = field(default_factory=dict, repr=False)

    @classmethod
    def load(cls, seasons: Iterable[int], max_age_hours: float | None = None, use_derived_cache: bool = True,
             log=None) -> "CFBData":
        seasons = sorted(set(int(s) for s in seasons))
        L, T, R, SCH, BET, TEAMS, VAL, RAW = [], [], [], [], [], [], {}, []
        pbp_lines = []
        for s in seasons:
            sched = cfbdata.load_season("schedules", s, max_age_hours)
            ros = cfbdata.load("rosters", [s], max_age_hours, optional=True)
            teams = cfbdata.load("teams", [s], max_age_hours, optional=True)
            box_raw = cfbdata.load("player_box", [s], max_age_hours, optional=True)
            tbox = cfbdata.load("team_box", [s], max_age_hours, optional=True)
            bet = cfbdata.load("betting", [s], max_age_hours, optional=True)
            lines, tt, flagged, val, lines_src, raw = _season_lines(s, ros, box_raw, tbox, max_age_hours,
                                                                    use_derived_cache, log)
            pbp_lines.append(lines_src)
            RAW.append(raw)
            L.append(lines); T.append(tt); R.append(ros); SCH.append(sched); BET.append(bet); TEAMS.append(teams)
            VAL[s] = val
        sched = pd.concat(SCH, ignore_index=True)
        bet = pd.concat([b for b in BET if len(b)], ignore_index=True) if any(len(b) for b in BET) else pd.DataFrame()
        pl = pd.concat([p for p in pbp_lines if p is not None and len(p)], ignore_index=True) if pbp_lines else None
        gl = cfbdata.game_lines(bet, pl)
        rosters = pd.concat([r for r in R if len(r)], ignore_index=True) if any(len(r) for r in R) else pd.DataFrame()
        teams = pd.concat([t for t in TEAMS if len(t)], ignore_index=True) if any(len(t) for t in TEAMS) else pd.DataFrame()
        out = cls.build(seasons, pd.concat(L, ignore_index=True), pd.concat(T, ignore_index=True), sched, gl,
                        rosters, teams, VAL)
        out.raw_lines = pd.concat([r for r in RAW if r is not None and len(r)], ignore_index=True) \
            if any(r is not None and len(r) for r in RAW) else pd.DataFrame()
        return out

    @classmethod
    def build(cls, seasons, lines, team_totals, schedules, game_lines, rosters, teams, validation=None) -> "CFBData":
        lines, team_totals = lines.copy(), team_totals.copy()
        # frames without competitive-snap counts (older caches, hand-built tests): use all snaps
        for c, src in (("c_pass_att", "pass_att"), ("c_sacks", "sacks"), ("c_carries", "carries"), ("c_targets", "targets")):
            if c not in lines.columns:
                lines[c] = lines[src]
        for c, src in (("team_c_pass_att", "team_pass_att"), ("team_c_dropbacks", "team_dropbacks"),
                       ("team_c_carries", "team_carries")):
            if c not in team_totals.columns:
                team_totals[c] = team_totals[src]
        if "usage_ok" not in team_totals.columns:
            team_totals["usage_ok"] = True
        tg = team_games_frame(schedules, game_lines)
        lines = lines.drop(columns=[c for c in ("pos", "pos_src") if c in lines.columns])
        pos = positions_frame(lines, rosters)
        lines = lines.merge(pos, on=["season", "athlete_id"], how="left")
        lines["pos"] = lines["pos"].fillna("WR")
        lines["pos_src"] = lines["pos_src"].fillna("usage")
        return cls(seasons=list(seasons), lines=lines, team_totals=team_totals, team_games=tg, game_lines=game_lines,
                   positions=pos, rosters=rosters, teams=teams, schedules=schedules, validation=validation or {})

    def points(self, table_id: str) -> pd.Series:
        """Fantasy points per line under ``table_id`` (cached)."""
        key = ("fp", table_id)
        if key not in self.cache:
            self.cache[key] = cs.fantasy_points(self.lines, table_id, "rush_yds" if SACKS_IN_RUSHING else "carry_yds")
        return self.cache[key]


def _season_lines(season, rosters, box_raw, team_box, max_age_hours, use_cache, log):
    """Stat lines for one season, cached as parquet keyed by the source files' mtimes."""
    src = cfbdata.cache_path("pbp", season)
    if not src.exists() or cfbdata._is_stale(src, season, cfbdata.DEFAULT_MAX_AGE_H if max_age_hours is None else max_age_hours):
        cfbdata.load_season("pbp", season, max_age_hours, columns=["game_id"])
    stamp = "|".join(f"{p.name}:{p.stat().st_mtime_ns}" for p in
                     [cfbdata.cache_path(n, season) for n in ("pbp", "rosters", "player_box", "team_box")] if p.exists())
    h = hashlib.sha256(f"{DERIVED_VERSION}|{stamp}".encode()).hexdigest()[:12]
    d = cfbdata.CACHE_DIR / "derived"
    f_lines, f_tt, f_meta, f_pl, f_raw = (d / f"lines_{season}_{h}.parquet", d / f"team_totals_{season}_{h}.parquet",
                                          d / f"meta_{season}_{h}.json", d / f"pbp_lines_{season}_{h}.parquet",
                                          d / f"raw_lines_{season}_{h}.parquet")
    if use_cache and f_lines.exists() and f_tt.exists() and f_meta.exists() and f_raw.exists():
        meta = json.loads(f_meta.read_text())
        pl = pd.read_parquet(f_pl) if f_pl.exists() else None
        return (pd.read_parquet(f_lines), pd.read_parquet(f_tt), pd.DataFrame(meta.get("flagged", [])),
                meta.get("validation", {}), pl, pd.read_parquet(f_raw))
    if log:
        log(f"cfb: building {season} stat lines from play-by-play")
    pbp = cfbdata.load_season("pbp", season, max_age_hours)
    lines, tt, stats = cs.player_game_lines(pbp, rosters)
    box = cs.box_lines(box_raw) if box_raw is not None and len(box_raw) else pd.DataFrame()
    fixed, flagged = cs.repair_with_box(lines, tt, box, team_box)
    tt = tt.copy()
    bad = set(zip(flagged["game_id"], flagged["team_id"])) if len(flagged) else set()
    tt["usage_ok"] = [(g, t) not in bad for g, t in zip(tt["game_id"], tt["team_id"])]
    val = {"attribution": stats.summary(), "flagged_team_games": int(len(flagged))}
    if len(box):
        val["box_before_repair"] = cs.validate_against_box(lines, box)
        val["box_after_repair"] = cs.validate_against_box(fixed, box)
    pl = pbp[["game_id", "spread", "over_under"]].drop_duplicates("game_id") if "spread" in pbp.columns else None
    d.mkdir(parents=True, exist_ok=True)
    for old in d.glob(f"*_{season}_*"):
        if h not in old.name:
            old.unlink(missing_ok=True)
    fixed.to_parquet(f_lines, index=False)
    lines.to_parquet(f_raw, index=False)
    tt.to_parquet(f_tt, index=False)
    if pl is not None:
        pl.to_parquet(f_pl, index=False)
    f_meta.write_text(json.dumps({"flagged": flagged.to_dict("records"), "validation": val}, default=float))
    return fixed, tt, flagged, val, pl, lines


def team_games_frame(schedules: pd.DataFrame, game_lines: pd.DataFrame | None) -> pd.DataFrame:
    """One row per team per scheduled game: opponent, points (NaN until played), implied points."""
    s = schedules.dropna(subset=["home_id", "away_id"]).copy()
    s["home_id"] = s["home_id"].astype("int64")
    s["away_id"] = s["away_id"].astype("int64")
    if game_lines is not None and len(game_lines):
        s = s.merge(game_lines[["game_id", "home_spread", "total", "line_source"]], on="game_id", how="left")
    else:
        s["home_spread"] = np.nan; s["total"] = np.nan; s["line_source"] = None
    hi, ai = cfbdata.implied_points(s["home_spread"], s["total"])
    common = [c for c in ["season", "week", "game_id", "start_date", "neutral_site", "conference_game", "completed",
                          "venue", "home_spread", "total", "line_source", "fbs_game"] if c in s.columns]
    home = s[common].assign(team_id=s["home_id"], opp_id=s["away_id"], team=s["home_team"], opp=s["away_team"],
                            is_home=1, points=s.get("home_points"), opp_points=s.get("away_points"), implied=hi,
                            opp_implied=ai, team_division=s.get("home_division"))
    away = s[common].assign(team_id=s["away_id"], opp_id=s["home_id"], team=s["away_team"], opp=s["home_team"],
                            is_home=0, points=s.get("away_points"), opp_points=s.get("home_points"), implied=ai,
                            opp_implied=hi, team_division=s.get("away_division"))
    return pd.concat([home, away], ignore_index=True).sort_values(["season", "week", "game_id", "is_home"]).reset_index(drop=True)


def _infer_pos(use: pd.DataFrame) -> np.ndarray:
    """QB / RB / WR from summed pass attempts, carries and targets."""
    return np.where(use["pass_att"] >= np.maximum(5, use["carries"]), "QB",
                    np.where(use["carries"] > use["targets"], "RB", "WR"))


def positions_frame(lines: pd.DataFrame, rosters: pd.DataFrame | None) -> pd.DataFrame:
    """(season, athlete_id) -> pos (QB/RB/WR/TE) and pos_src: roster position ("roster"), else
    inferred from the season's usage ("usage"; recomputed point in time by ``point_in_time_pos``)."""
    use = lines.groupby(["season", "athlete_id"])[["pass_att", "carries", "targets"]].sum()
    out = pd.DataFrame({"pos_used": _infer_pos(use)}, index=use.index).reset_index()
    if rosters is not None and len(rosters):
        r = rosters[["season", "athlete_id", "position_abbreviation"]].dropna(subset=["athlete_id"]).copy()
        r["athlete_id"] = r["athlete_id"].astype("int64")
        r["pos_roster"] = r["position_abbreviation"].map(ROSTER_POS)
        r = r.drop_duplicates(["season", "athlete_id"])
        out = out.merge(r[["season", "athlete_id", "pos_roster"]], on=["season", "athlete_id"], how="left")
        # roster positions are a season snapshot; fall back to any season's roster entry
        anyr = r.dropna(subset=["pos_roster"]).sort_values("season").drop_duplicates("athlete_id", keep="last")
        out = out.merge(anyr[["athlete_id", "pos_roster"]].rename(columns={"pos_roster": "pos_any"}), on="athlete_id", how="left")
        known = out["pos_roster"].fillna(out["pos_any"])
        out["pos"] = known.fillna(out["pos_used"])
        out["pos_src"] = np.where(known.notna(), "roster", "usage")
    else:
        out["pos"] = out["pos_used"]
        out["pos_src"] = "usage"
    return out[["season", "athlete_id", "pos", "pos_src"]]


def point_in_time_pos(hist: pd.DataFrame) -> pd.Series:
    """Positions for history rows: roster positions as they are; usage-inferred ones recomputed from
    ``hist`` itself (rows before the target week), so later games never relabel a player."""
    if "pos_src" not in hist.columns or not (hist["pos_src"] == "usage").any():
        return hist["pos"]
    u = hist[hist["pos_src"] == "usage"]
    use = u.groupby(["season", "athlete_id"])[["pass_att", "carries", "targets"]].sum()
    inferred = pd.Series(_infer_pos(use), index=use.index)
    key = pd.MultiIndex.from_arrays([hist["season"], hist["athlete_id"]])
    new = pd.Series(inferred.reindex(key).to_numpy(), index=hist.index)
    return new.where(hist["pos_src"] == "usage", hist["pos"]).fillna(hist["pos"])


# --------------------------------------------------------------------------- features (point in time)


TEAM_USAGE_COLS = ["team_pass_att", "team_dropbacks", "team_carries", "team_c_pass_att", "team_c_dropbacks", "team_c_carries"]


def _opps(df: pd.DataFrame, pos: pd.Series, basis: str = "all") -> tuple[pd.Series, pd.Series]:
    """(player opportunities, team opportunities) for each row, by position.

    QB: dropbacks / team dropbacks; RB: carries + targets / team carries + pass attempts; WR/TE:
    targets / team pass attempts. ``basis="competitive"`` counts non-garbage-time snaps only.
    """
    c = "c_" if basis == "competitive" else ""
    tc = "team_c_" if basis == "competitive" else "team_"
    g = lambda col: df[col] if col in df.columns else pd.Series(0.0, index=df.index)
    qb = pos == "QB"
    rb = pos == "RB"
    player = np.where(qb, g(f"{c}pass_att") + g(f"{c}sacks"), np.where(rb, g(f"{c}carries") + g(f"{c}targets"), g(f"{c}targets")))
    team = np.where(qb, g(f"{tc}dropbacks"), np.where(rb, g(f"{tc}carries") + g(f"{tc}pass_att"), g(f"{tc}pass_att")))
    return pd.Series(player, index=df.index, dtype=float), pd.Series(team, index=df.index, dtype=float)


def features(data: CFBData, season: int, week: int, table_id: str, params: CFBParams | None = None,
             team_ids: Iterable[int] | None = None) -> pd.DataFrame:
    """One row per player with a recent role on a team that plays in (season, week).

    Uses only rows strictly before (season, week). Columns: athlete_id, name, team_id, pos, game_id,
    opp_id, n_s, fpg_s, n_prev, fpg_prev, season_share, recent_opps, recent_team_opps, recent_share,
    appeared_last, last_fp, team_ppg, team_n, implied, team_opps_pg, exp_opps.
    """
    params = params or CFBParams()
    fp = data.points(table_id)
    L = data.lines.assign(fp=fp)
    hist = cfbdata.before(L, season, week)
    hist = hist.assign(pos=point_in_time_pos(hist))
    cur = hist[hist["season"] == season]
    prev = hist[hist["season"] == season - 1]
    tgw = data.team_games[(data.team_games["season"] == season) & (data.team_games["week"] == week)]
    if team_ids is not None:
        tgw = tgw[tgw["team_id"].isin(set(int(t) for t in team_ids))]
    tgw = tgw.drop_duplicates("team_id")
    if tgw.empty or cur.empty:
        return pd.DataFrame()
    playing = set(tgw["team_id"])

    # current team = team of the player's latest current-season game
    cur = cur.sort_values(["week", "game_id"])
    latest = cur.drop_duplicates("athlete_id", keep="last")[["athlete_id", "team_id", "name", "pos"]]
    latest = latest[latest["team_id"].isin(playing)]
    if latest.empty:
        return pd.DataFrame()
    tt = data.team_totals
    tt_cur = cfbdata.before(tt[tt["season"] == season], season, week)
    cur = cur.drop(columns=[c for c in ("usage_ok",) if c in cur.columns]).merge(
        tt_cur[["game_id", "team_id", *TEAM_USAGE_COLS, "usage_ok"]], on=["game_id", "team_id"], how="left")
    for c in TEAM_USAGE_COLS:
        cur[c] = cur[c].fillna(0.0)
    cur["usage_ok"] = cur["usage_ok"].fillna(False).astype(bool)
    cur["pos"] = cur["athlete_id"].map(latest.set_index("athlete_id")["pos"]).fillna(cur["pos"])
    basis = params.usage_basis
    po, to = _opps(cur, cur["pos"], basis)
    cur["opps"], cur["team_opps"] = po, to
    cur = cur[cur["athlete_id"].isin(latest["athlete_id"])]

    g = cur.groupby("athlete_id")
    feat = latest.set_index("athlete_id").copy()
    feat["n_s"] = g.size()
    feat["fpg_s"] = g["fp"].mean()
    ok = cur[cur["usage_ok"]]
    go = ok.groupby("athlete_id")
    feat["season_opps"] = go["opps"].sum()
    feat["season_team_opps"] = go["team_opps"].sum()
    feat["season_share"] = (feat["season_opps"] / feat["season_team_opps"].replace(0, np.nan)).fillna(0.0)
    last_game = cur.sort_values(["week", "game_id"]).groupby("athlete_id").tail(1).set_index("athlete_id")
    feat["last_fp"] = last_game["fp"]
    feat["last_week"] = last_game["week"]

    # team's last R games (usage_ok) and latest game
    tt_team = tt_cur[tt_cur["team_id"].isin(playing)].sort_values(["week", "game_id"])
    last_any = tt_team.groupby("team_id").tail(1)[["team_id", "game_id"]].rename(columns={"game_id": "last_game_id"})
    recent = tt_team[tt_team["usage_ok"]].groupby("team_id").tail(params.recent_games)
    feat = feat.reset_index().merge(last_any, on="team_id", how="left")
    # recent opportunities: player rows in those games (0 when absent)
    rg = recent[["team_id", "game_id", *TEAM_USAGE_COLS]]
    pr = feat[["athlete_id", "team_id", "pos"]].merge(rg, on="team_id", how="left")
    pl = cur[["athlete_id", "game_id", "opps"]]
    pr = pr.merge(pl, on=["athlete_id", "game_id"], how="left")
    pr["appeared"] = pr["opps"].notna()
    pr["opps"] = pr["opps"].fillna(0.0)
    _, pr["team_opps"] = _opps(pr, pr["pos"], basis)
    pr["team_opps_played"] = pr["team_opps"].where(pr["appeared"], 0.0)
    agg = pr.groupby("athlete_id").agg(recent_opps=("opps", "sum"), recent_team_opps=("team_opps", "sum"),
                                       recent_team_opps_played=("team_opps_played", "sum"),
                                       recent_games_n=("game_id", "nunique"), recent_appear=("appeared", "sum"))
    feat = feat.merge(agg, on="athlete_id", how="left")
    appeared_any = set(zip(cur["athlete_id"], cur["game_id"]))
    feat["appeared_last"] = [(a, g_) in appeared_any for a, g_ in zip(feat["athlete_id"], feat["last_game_id"])]
    # appearance in any of the team's last R games (incl. usage-flagged ones)
    lastR_any = tt_team.groupby("team_id").tail(params.recent_games)[["team_id", "game_id"]]
    lr = feat[["athlete_id", "team_id"]].merge(lastR_any, on="team_id")
    lr["hit"] = [(a, g_) in appeared_any for a, g_ in zip(lr["athlete_id"], lr["game_id"])]
    feat["appeared_recent"] = feat["athlete_id"].map(lr.groupby("athlete_id")["hit"].any()).fillna(False).astype(bool)
    denom = feat["recent_team_opps_played"] if params.recent_mode == "played" else feat["recent_team_opps"]
    feat["recent_share"] = ((feat["recent_opps"] + params.k_recent * feat["season_share"])
                            / (denom.fillna(0) + params.k_recent))
    feat["recent_share_raw"] = (feat["recent_opps"] / feat["recent_team_opps"].replace(0, np.nan)).fillna(0.0)

    # depth: rank within the team's QB / RB / WR+TE group by recent usage (players with a recent role)
    grp = np.where(feat["pos"].isin(["WR", "TE"]), "WRTE", feat["pos"])
    act = feat["appeared_recent"].astype(bool)
    order = feat.assign(_g=grp, _a=act).sort_values(["team_id", "_g", "_a", "recent_share_raw", "season_share"],
                                                     ascending=[True, True, False, False, False])
    order["_d"] = order.groupby(["team_id", "_g"]).cumcount() + 1
    feat["depth"] = order["_d"].where(order["_a"]).reindex(feat.index)

    # prior season (any team)
    if len(prev):
        gp = prev.groupby("athlete_id")["fp"]
        feat["n_prev"] = feat["athlete_id"].map(gp.size()).fillna(0).astype(int)
        feat["fpg_prev"] = feat["athlete_id"].map(gp.mean())
    else:
        feat["n_prev"] = 0
        feat["fpg_prev"] = np.nan

    # team volume and scoring (current season, before the week)
    tgc = cfbdata.before(data.team_games[(data.team_games["season"] == season)], season, week)
    tgc = tgc[tgc["points"].notna()]
    team_pts = tgc.groupby("team_id")["points"].agg(["mean", "size"])
    feat["team_ppg"] = feat["team_id"].map(team_pts["mean"])
    feat["team_n"] = feat["team_id"].map(team_pts["size"]).fillna(0)
    league_ppg = float(tgc.loc[tgc["fbs_game"].fillna(False).astype(bool), "points"].mean()) if "fbs_game" in tgc.columns \
        and tgc["fbs_game"].fillna(False).any() else float(tgc["points"].mean())
    feat["league_ppg"] = league_ppg
    ok_tt = tt_team[tt_team["usage_ok"]]
    vol = ok_tt.groupby("team_id")[TEAM_USAGE_COLS].mean()
    v = feat[["team_id", "pos"]].merge(vol, left_on="team_id", right_index=True, how="left").fillna(0.0)
    _, feat["team_opps_pg"] = _opps(v, v["pos"], basis)
    feat["exp_opps"] = feat["recent_share"] * feat["team_opps_pg"]

    # target game
    feat = feat.merge(tgw[["team_id", "game_id", "opp_id", "team", "opp", "is_home", "implied", "home_spread", "total",
                           "line_source", "start_date", "neutral_site"]], on="team_id", how="left")
    feat["season"], feat["week"] = season, week
    return feat


def role_prior_slope(data: CFBData, season: int, week: int, table_id: str, basis: str = "competitive") -> dict[str, float]:
    """Points per game per unit of season usage share, by position, from player-seasons before the target."""
    key = ("slope", season, week, table_id, basis)
    if key in data.cache:
        return data.cache[key]
    fp = data.points(table_id)
    L = cfbdata.before(data.lines.assign(fp=fp), season, week)
    L = L[L["season"] >= season - 1]
    L = L.assign(pos=point_in_time_pos(L))
    L = L.drop(columns=[c for c in ("usage_ok",) if c in L.columns]).merge(
        data.team_totals[["game_id", "team_id", *TEAM_USAGE_COLS, "usage_ok"]], on=["game_id", "team_id"], how="left")
    L = L[L["usage_ok"].fillna(False).astype(bool)]
    po, to = _opps(L, L["pos"], basis)
    L = L.assign(opps=po, team_opps=to)
    ps = L.groupby(["season", "athlete_id", "pos"]).agg(fp=("fp", "mean"), n=("fp", "size"), o=("opps", "sum"),
                                                        t=("team_opps", "sum")).reset_index()
    ps = ps[ps["t"] > 0]
    ps["share"] = ps["o"] / ps["t"]
    out = {}
    for pos, d in ps.groupby("pos"):
        w = d["n"]
        denom = float((w * d["share"] ** 2).sum())
        out[pos] = float((w * d["share"] * d["fp"]).sum() / denom) if denom > 0 else 0.0
    data.cache[key] = out
    return out


# --------------------------------------------------------------------------- projection


def project(feat: pd.DataFrame, slope: Mapping[str, float], params: CFBParams | None = None,
            dispersion: Mapping[str, Mapping[str, float]] | None = None,
            pzero: Mapping[str, Mapping[str, list[float]]] | None = None) -> pd.DataFrame:
    """Means, p_zero and quantiles from ``features`` output. Rows without a recent role are dropped
    (returned separately via ``no_recent_role``)."""
    params = params or CFBParams()
    dispersion = dispersion or DEFAULT_DISPERSION
    pzero = pzero or DEFAULT_PZERO
    if feat is None or feat.empty:
        return pd.DataFrame()
    f = feat[feat["appeared_recent"]].copy()
    if f.empty:
        return f
    prior_role = f["pos"].map(lambda p: slope.get(p, slope.get("WR", 0.0))) * f["season_share"]
    wp = params.w_prev * np.minimum(f["n_prev"].fillna(0), params.cap_prev)
    fpg_prev = f["fpg_prev"].fillna(0.0)
    f["prior_role"] = prior_role
    f["base"] = ((f["n_s"] * f["fpg_s"] + wp * fpg_prev + params.k0 * prior_role)
                 / (f["n_s"] + wp + params.k0))
    ratio = (f["recent_share"] / f["season_share"].replace(0, np.nan)).fillna(1.0)
    r = np.clip(ratio, params.ratio_lo, params.ratio_hi)
    f["workload"] = np.where(r >= 1.0, r ** params.alpha, r ** params.alpha_down)
    team_avg = ((f["team_n"] * f["team_ppg"].fillna(f["league_ppg"]) + params.k_team * f["league_ppg"])
                / (f["team_n"] + params.k_team))
    env = (f["implied"] / team_avg).where(f["implied"].notna() & (team_avg > 0), 1.0)
    f["env"] = np.clip(env, params.env_lo, params.env_hi) ** params.beta
    f["team_avg_shrunk"] = team_avg
    scale = f["pos"].map(lambda p: params.scale.get(p, 1.0))
    f["mean_played"] = np.maximum(f["base"] * f["workload"] * f["env"] * scale, 0.0)
    # availability
    f["p_zero"] = p_zero_from(f, pzero)
    f["mean"] = (1 - f["p_zero"]) * f["mean_played"]
    # spread
    q = [quantiles_from_mean_sd(m, dispersion.get(p, dispersion["WR"])["a"] + dispersion.get(p, dispersion["WR"])["b"] * m,
                                dispersion.get(p, dispersion["WR"]).get("skew", 0.3))
         for m, p in zip(f["mean_played"], f["pos"])]
    f["p10"], f["p50"], f["p90"] = zip(*q) if q else ([], [], [])
    f["sd"] = [dispersion.get(p, dispersion["WR"])["a"] + dispersion.get(p, dispersion["WR"])["b"] * m
               for m, p in zip(f["mean_played"], f["pos"])]
    return f


def pz_design(f: pd.DataFrame) -> np.ndarray:
    missed = (~f["appeared_last"].astype(bool)).astype(float).to_numpy()
    lx = np.log1p(f["exp_opps"].fillna(0.0).clip(lower=0.0).to_numpy(float))
    depth = f["depth"].fillna(99).to_numpy(float) if "depth" in f.columns else np.full(len(f), 99.0)
    return np.column_stack([np.ones(len(f)), missed, lx, depth == 1, depth == 2, lx ** 2, missed * lx]).astype(float)


def p_zero_from(f: pd.DataFrame, pzero: Mapping[str, Mapping[str, list[float]]] | None = None) -> np.ndarray:
    """P(no recorded stat) per row from the per-position logistic coefficients."""
    pzero = pzero or DEFAULT_PZERO
    X = pz_design(f)
    out = np.zeros(len(f))
    pos = f["pos"].to_numpy()
    for p in np.unique(pos):
        coef = np.asarray(pzero.get(p, pzero.get("WR"))["coef"], dtype=float)
        idx = pos == p
        out[idx] = 1.0 / (1.0 + np.exp(-(X[idx] @ coef)))
    return np.clip(out, 0.0, 0.97)


def no_recent_role(feat: pd.DataFrame) -> pd.DataFrame:
    return feat[~feat["appeared_recent"]] if feat is not None and len(feat) else pd.DataFrame()


def project_week(data: CFBData, season: int, week: int, table_id: str, params: CFBParams | None = None,
                 team_ids: Iterable[int] | None = None, dispersion=None, pzero=None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(projections, removed-for-no-recent-role) for every player on teams playing in (season, week)."""
    params = params or CFBParams()
    feat = features(data, season, week, table_id, params, team_ids)
    if feat.empty:
        return feat, feat
    slope = role_prior_slope(data, season, week, table_id, params.usage_basis)
    return project(feat, slope, params, dispersion, pzero), no_recent_role(feat)


# --------------------------------------------------------------------------- fitting dispersion / availability


def actuals(data: CFBData, season: int, week: int, table_id: str) -> pd.DataFrame:
    """Target-week outcomes: points and volume per (athlete_id, team_id) for the week's games."""
    L = data.lines[(data.lines["season"] == season) & (data.lines["week"] == week)].copy()
    L["fp"] = data.points(table_id).loc[L.index]
    L["touches"] = L["carries"] + L["targets"]
    L["qb_plays"] = L["pass_att"] + L["sacks"] + L["carries"]
    return L[["athlete_id", "team_id", "game_id", "fp", "touches", "qb_plays", "carries", "targets", "pass_att",
              "stat_source"]]


def fit_dispersion(rows: pd.DataFrame, skews: Iterable[float] = (0.0, 0.15, 0.3, 0.45, 0.6, 0.75)) -> dict:
    """SD = a + b*mean by position from played-outcome residuals; skew chosen for 10/90 balance.

    ``rows`` need: pos, mean_played, actual (players who recorded a stat).
    """
    out = {}
    for pos, d in rows.groupby("pos"):
        m, y = d["mean_played"].to_numpy(float), d["actual"].to_numpy(float)
        r2 = (y - m) ** 2
        X = np.column_stack([np.ones_like(m), m])
        # fit variance-ish: regress |resid| * sqrt(pi/2) on mean (robust to fat tails vs squared)
        absr = np.abs(y - m) * np.sqrt(np.pi / 2)
        coef, *_ = np.linalg.lstsq(X, absr, rcond=None)
        a, b = max(float(coef[0]), 0.5), max(float(coef[1]), 0.05)
        # rescale so that mean predicted variance matches the empirical residual variance
        sd = a + b * m
        scale = float(np.sqrt(r2.mean() / np.mean(sd ** 2)))
        a, b = a * scale, b * scale
        best = None
        for sk in skews:
            q = np.array([quantiles_from_mean_sd(mi, a + b * mi, sk) for mi in m])
            lo, hi = float((y < q[:, 0]).mean()), float((y > q[:, 2]).mean())
            err = abs(lo - 0.1) + abs(hi - 0.1)
            if best is None or err < best[0]:
                best = (err, sk, lo, hi)
        out[pos] = {"a": round(a, 3), "b": round(b, 3), "skew": best[1], "fit_below_p10": round(best[2], 3),
                    "fit_above_p90": round(best[3], 3), "n": int(len(d))}
    return out


def fit_pzero(rows: pd.DataFrame, l2: float = 1.0, iters: int = 50) -> dict:
    """Per-position logistic regression of "no recorded stat" on ``PZ_FEATURES`` (Newton/IRLS, L2 on slopes).

    ``rows`` need: pos, appeared_last, exp_opps, depth, played (bool).
    """
    out = {}
    for pos in POSITIONS:
        d = rows[rows["pos"] == pos]
        if len(d) < 50:
            out[pos] = DEFAULT_PZERO[pos]
            continue
        X = pz_design(d)
        y = (~d["played"].astype(bool)).astype(float).to_numpy()
        w = np.zeros(X.shape[1])
        w[0] = np.log(max(y.mean(), 1e-3) / max(1 - y.mean(), 1e-3))
        reg = np.full(X.shape[1], l2)
        reg[0] = 0.0
        for _ in range(iters):
            pr = 1.0 / (1.0 + np.exp(-(X @ w)))
            g = X.T @ (pr - y) + reg * w
            H = (X * (pr * (1 - pr))[:, None]).T @ X + np.diag(reg) + 1e-9 * np.eye(len(w))
            step = np.linalg.solve(H, g)
            w -= step
            if np.abs(step).max() < 1e-8:
                break
        pr = 1.0 / (1.0 + np.exp(-(X @ w)))
        out[pos] = {"coef": [round(float(x), 4) for x in w], "n": int(len(d)), "absent_rate": round(float(y.mean()), 4),
                    "mean_pred": round(float(pr.mean()), 4)}
    return out


def params_dict(p: CFBParams) -> dict:
    return asdict(p)
