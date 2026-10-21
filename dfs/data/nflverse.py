"""Cached nflverse loaders (via nflreadpy) returning pandas, plus point-in-time helpers.

Every dataset is cached per season as parquet under ``data/cache/nflverse``. Past seasons are
cached forever; the current season is re-downloaded when its file is older than
``max_age_hours`` (default ``DFS_NFLVERSE_MAX_AGE_H`` or 6). The helpers below are the only
way projection code reads history, so target-week results never leak into features:
``before()`` keeps rows strictly earlier than (season, week), and the target-week helpers
return only what is known before kickoff (schedule + lines, injury report, roster status,
the last depth-chart snapshot before game day).
"""

from __future__ import annotations

import os
import time
import warnings
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable

import numpy as np
import pandas as pd

from dfs.data.teams import canonicalize

REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = Path(os.environ.get("DFS_CACHE_DIR", REPO_ROOT / "data" / "cache")) / "nflverse"
DEFAULT_MAX_AGE_H = float(os.environ.get("DFS_NFLVERSE_MAX_AGE_H", "6"))
FIRST_SEASON = 2016


def _nfl():
    import nflreadpy  # imported lazily so offline code paths never need it

    return nflreadpy


def _loader(name: str) -> Callable[[int], "object"]:
    nfl = _nfl()
    table: dict[str, Callable[[int], object]] = {
        "schedules": lambda s: nfl.load_schedules([s]),
        "player_stats": lambda s: nfl.load_player_stats([s], summary_level="week"),
        "team_stats": lambda s: nfl.load_team_stats([s], summary_level="week"),
        "snap_counts": lambda s: nfl.load_snap_counts([s]),
        "injuries": lambda s: nfl.load_injuries([s]),
        "depth_charts": lambda s: nfl.load_depth_charts([s]),
        "rosters_weekly": lambda s: nfl.load_rosters_weekly([s]),
        "ff_opportunity": lambda s: nfl.load_ff_opportunity([s], stat_type="weekly"),
    }
    if name not in table:
        raise KeyError(f"unknown nflverse dataset {name!r}; known: {sorted(table)}")
    return table[name]


DATASETS = ("schedules", "player_stats", "team_stats", "snap_counts", "injuries", "depth_charts",
            "rosters_weekly", "ff_opportunity")


def current_season(today: date | None = None) -> int:
    """NFL season year: the calendar year from the Wednesday after Labor Day (the 2026 opener was
    Wednesday 9 Sep), else the year before."""
    today = today or date.today()
    y = today.year
    labor = next(date(y, 9, d) for d in range(1, 8) if date(y, 9, d).weekday() == 0)
    start = date(y, 9, labor.day + 2)
    return y if today >= start else y - 1


def cache_path(name: str, season: int | None) -> Path:
    return CACHE_DIR / (f"{name}_{season}.parquet" if season is not None else f"{name}.parquet")


def _is_stale(path: Path, season: int | None, max_age_hours: float) -> bool:
    if not path.exists():
        return True
    if season is not None and season < current_season():
        return False
    return (time.time() - path.stat().st_mtime) > max_age_hours * 3600.0


def _to_pandas(frame) -> pd.DataFrame:
    df = frame.to_pandas() if hasattr(frame, "to_pandas") else pd.DataFrame(frame)
    return _normalize_keys(df)


def _normalize_keys(df: pd.DataFrame) -> pd.DataFrame:
    """Integer season/week everywhere (ff_opportunity ships them as str / float)."""
    for col in ("season", "week"):
        if col in df.columns and not pd.api.types.is_integer_dtype(df[col]):
            v = pd.to_numeric(df[col], errors="coerce")
            df[col] = v.astype("int64") if v.notna().all() else v.astype("Int64")
    return df


@lru_cache(maxsize=256)
def _read_parquet(path: str, mtime: float) -> pd.DataFrame:
    return pd.read_parquet(path)


def load_season(name: str, season: int, max_age_hours: float | None = None,
                refresh: bool = False) -> pd.DataFrame:
    """One season of one dataset, from the parquet cache when fresh enough."""
    max_age = DEFAULT_MAX_AGE_H if max_age_hours is None else max_age_hours
    path = cache_path(name, season)
    if refresh or _is_stale(path, season, max_age):
        try:
            df = _to_pandas(_loader(name)(season))
        except Exception as exc:  # network / upstream failure: a stale cache beats no data
            if not path.exists():
                raise
            age_h = (time.time() - path.stat().st_mtime) / 3600.0
            warnings.warn(f"nflverse {name} {season}: refresh failed ({exc!r}); using cache {age_h:.1f}h old", stacklevel=2)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp.parquet")
            df.to_parquet(tmp, index=False)
            tmp.replace(path)
    return _normalize_keys(_read_parquet(str(path), path.stat().st_mtime).copy())


def load(name: str, seasons: int | Iterable[int], max_age_hours: float | None = None,
         refresh: bool = False) -> pd.DataFrame:
    """Concatenate cached seasons of an nflverse dataset into one pandas frame."""
    seasons = [seasons] if isinstance(seasons, int) else list(seasons)
    frames = [load_season(name, s, max_age_hours, refresh) for s in seasons]
    frames = [f for f in frames if len(f)]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def load_players(max_age_hours: float | None = None, refresh: bool = False) -> pd.DataFrame:
    """nflverse players table (id crosswalk: gsis_id, pfr_id, espn_id, ...)."""
    max_age = DEFAULT_MAX_AGE_H * 4 if max_age_hours is None else max_age_hours
    path = cache_path("players", None)
    if refresh or not path.exists() or (time.time() - path.stat().st_mtime) > max_age * 3600.0:
        df = _to_pandas(_nfl().load_players())
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, index=False)
    return _read_parquet(str(path), path.stat().st_mtime).copy()


# --------------------------------------------------------------------------- point-in-time helpers


def week_key(season, week) -> np.ndarray | int:
    """Sortable (season, week) index; week numbers include the postseason (19-22)."""
    return np.asarray(season, dtype=np.int64) * 100 + np.asarray(week, dtype=np.int64)


def before(df: pd.DataFrame, season: int, week: int, season_col: str = "season",
           week_col: str = "week", min_season: int | None = None) -> pd.DataFrame:
    """Rows strictly before (season, week). The target week itself is always excluded."""
    if df.empty:
        return df
    k = week_key(df[season_col].to_numpy(), df[week_col].fillna(0).to_numpy())
    mask = k < week_key(season, week)
    if min_season is not None:
        mask &= df[season_col].to_numpy() >= min_season
    return df.loc[mask]


def implied_points(spread_line, total_line):
    """nflverse ``spread_line`` > 0 means the HOME team is favoured by that many points.

    Returns (home_implied, away_implied) = ((total + spread)/2, (total - spread)/2).
    """
    s = np.asarray(spread_line, dtype=float)
    t = np.asarray(total_line, dtype=float)
    return (t + s) / 2.0, (t - s) / 2.0


def team_games(schedule: pd.DataFrame) -> pd.DataFrame:
    """One row per team per game from a schedule frame, with implied points and the result."""
    s = schedule.copy()
    home_imp, away_imp = implied_points(s["spread_line"], s["total_line"])
    common = ["game_id", "season", "week", "game_type", "gameday", "gametime", "spread_line", "total_line", "roof"]
    common = [c for c in common if c in s.columns]
    home = s[common].assign(team=s["home_team"], opponent=s["away_team"], is_home=1, implied=home_imp,
                            opp_implied=away_imp, team_spread=s["spread_line"].astype(float),
                            points=s["home_score"], opp_points=s["away_score"])
    away = s[common].assign(team=s["away_team"], opponent=s["home_team"], is_home=0, implied=away_imp,
                            opp_implied=home_imp, team_spread=-s["spread_line"].astype(float),
                            points=s["away_score"], opp_points=s["home_score"])
    out = pd.concat([home, away], ignore_index=True)
    out["team"] = out["team"].map(canonicalize)
    out["opponent"] = out["opponent"].map(canonicalize)
    return out.sort_values(["season", "week", "game_id", "is_home"]).reset_index(drop=True)


def week_schedule(season: int, week: int, schedules: pd.DataFrame | None = None) -> pd.DataFrame:
    """Target-week games (lines included; scores are dropped so nothing downstream can peek)."""
    s = schedules if schedules is not None else load("schedules", [season])
    s = s[(s["season"] == season) & (s["week"] == week)].copy()
    return s.drop(columns=[c for c in ("away_score", "home_score", "result", "total", "overtime") if c in s.columns])


def week_injuries(season: int, week: int, injuries: pd.DataFrame | None = None) -> pd.DataFrame:
    """The target week's final injury report rows (report_status, practice_status by gsis_id)."""
    inj = injuries if injuries is not None else load("injuries", [season])
    inj = inj[(inj["season"] == season) & (inj["week"] == week)].copy()
    if len(inj):
        inj["team"] = inj["team"].map(lambda t: canonicalize(t, strict=False))
    return inj


def week_roster(season: int, week: int, rosters: pd.DataFrame | None = None) -> pd.DataFrame:
    """The target week's roster rows (status ACT / INA / RES / DEV / CUT ...)."""
    r = rosters if rosters is not None else load("rosters_weekly", [season])
    r = r[(r["season"] == season) & (r["week"] == week)].copy()
    if len(r):
        r["team"] = r["team"].map(lambda t: canonicalize(t, strict=False))
    return r


_DC_POS = {"QB": "QB", "RB": "RB", "HB": "RB", "FB": "RB", "WR": "WR", "LWR": "WR", "RWR": "WR",
           "SWR": "WR", "TE": "TE", "PK": "K", "K": "K"}


def depth_chart_asof(season: int, as_of: datetime | str | None = None, week: int | None = None,
                     depth: pd.DataFrame | None = None) -> pd.DataFrame:
    """Offensive depth chart (team, gsis_id, pos, rank) known at ``as_of``.

    2025+ files are timestamped snapshots: the last snapshot strictly before ``as_of`` per team.
    Earlier files are weekly: the rows for ``week``. ``rank`` is 1 for the starter, ordinal per
    team and position (WR1, WR2, ... across alignments).
    """
    d = depth if depth is not None else load("depth_charts", [season])
    if d.empty:
        return pd.DataFrame(columns=["team", "gsis_id", "pos", "rank", "player_name"])
    if "dt" in d.columns:
        dt = pd.to_datetime(d["dt"], utc=True)
        if as_of is not None:
            cutoff = pd.Timestamp(as_of)
            cutoff = cutoff.tz_localize("UTC") if cutoff.tzinfo is None else cutoff.tz_convert("UTC")
            d = d.loc[dt < cutoff]
            dt = dt.loc[d.index]
        if d.empty:
            return pd.DataFrame(columns=["team", "gsis_id", "pos", "rank", "player_name"])
        last = dt.groupby(d["team"]).transform("max")
        d = d.loc[dt == last]
        d = d[~d["pos_grp"].isin(["Special Teams"]) | d["pos_abb"].isin(["PK"])]
        out = pd.DataFrame({"team": d["team"], "gsis_id": d["gsis_id"], "pos": d["pos_abb"].map(_DC_POS),
                            "raw_rank": d["pos_rank"].astype(float), "player_name": d["player_name"]})
    else:
        if week is None:
            raise ValueError("weekly depth charts need a week")
        d = d[(d["week"] == week) & (d["formation"].isin(["Offense", "Special Teams"]))]
        pos = d["depth_position"].astype(str).str.strip().map(_DC_POS)
        pos = pos.fillna(d["position"].map(_DC_POS))
        out = pd.DataFrame({"team": d["club_code"], "gsis_id": d["gsis_id"], "pos": pos,
                            "raw_rank": pd.to_numeric(d["depth_team"], errors="coerce"),
                            "player_name": d["full_name"]})
    out = out.dropna(subset=["pos", "gsis_id"])
    out["team"] = out["team"].map(lambda t: canonicalize(t, strict=False))
    out = out.sort_values(["team", "pos", "raw_rank"]).drop_duplicates(["team", "pos", "gsis_id"])
    out["rank"] = out.groupby(["team", "pos"]).cumcount() + 1
    return out.drop(columns="raw_rank").reset_index(drop=True)


def kickoff_utc(gameday: str, gametime: str | None) -> pd.Timestamp:
    """Schedule gameday + gametime (US Eastern) -> UTC timestamp."""
    t = gametime if isinstance(gametime, str) and gametime else "13:00"
    return pd.Timestamp(f"{gameday} {t}").tz_localize("America/New_York").tz_convert("UTC")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
