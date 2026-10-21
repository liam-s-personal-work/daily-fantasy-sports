"""Cached sportsdataverse college-football loaders (pandas) plus point-in-time helpers.

Files are the pre-built release parquets at
``github.com/sportsdataverse/sportsdataverse-data/releases`` (no scraping; registered as
``sportsdataverse`` in ``dfs.sources``). Every dataset is cached per season under
``data/cache/sportsdataverse``. Past seasons are cached forever; the current season is
re-downloaded when its file is older than ``max_age_hours`` (default ``DFS_CFB_MAX_AGE_H``
or 6). When a refresh fails (offline, host blocked) a stale cached copy is used and a
warning is recorded in ``LOAD_WARNINGS``; with no cached copy the error propagates.

Point-in-time rule: projection code reads history only through ``before()`` (rows strictly
earlier than (season, week)) and ``week_games()`` (target-week schedule without scores), so
target-week outcomes never leak into features.

Postseason weeks: the release files number bowl and playoff games from week 1 again
(``season_type == "postseason"``). ``_normalize`` moves them to ``POSTSEASON_WEEK_OFFSET + week``
(bowls = week 21) so they sort after the regular season; otherwise December/January games read
as week-1 history of the same season.
"""

from __future__ import annotations

import os
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from dfs.sources import require

REPO_ROOT = Path(__file__).resolve().parents[2]
# In a repo checkout the cache sits in data/cache; an installed copy uses the user's cache folder.
_DEFAULT_CACHE = (REPO_ROOT / "data" / "cache" if (REPO_ROOT / "pyproject.toml").exists()
                  else Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "dfs")
CACHE_DIR = Path(os.environ.get("DFS_CACHE_DIR", _DEFAULT_CACHE)) / "sportsdataverse"
DEFAULT_MAX_AGE_H = float(os.environ.get("DFS_CFB_MAX_AGE_H", "6"))
BASE_URL = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download"
USER_AGENT = "dfs-engine/0.1 (+sportsdataverse release files)"

# dataset -> (release tag, file name template)
DATASETS: dict[str, tuple[str, str]] = {
    "pbp": ("cfbfastR_cfb_pbp", "play_by_play_{season}.parquet"),
    "schedules": ("cfb_schedules", "cfb_schedules_{season}.parquet"),
    "betting": ("espn_cfb_betting", "betting_{season}.parquet"),
    "rosters": ("espn_cfb_rosters", "cfb_rosters_{season}.parquet"),
    "teams": ("espn_cfb_teams", "cfb_teams_{season}.parquet"),
    "player_box": ("espn_cfb_player_box", "player_box_{season}.parquet"),
    "team_box": ("espn_cfb_team_box", "team_box_{season}.parquet"),
    "passing": ("espn_cfb_passing", "cfb_passing_{season}.parquet"),
    "rushing": ("espn_cfb_rushing", "cfb_rushing_{season}.parquet"),
    "receiving": ("espn_cfb_receiving", "cfb_receiving_{season}.parquet"),
}

# The play-by-play file has ~360 columns; stat lines need these (read with column projection).
PBP_COLUMNS = [
    "season", "week", "season_type", "game_id", "id_play", "game_play_number", "drive_id", "pos_team", "def_pos_team",
    "home", "away", "play_type", "play_text", "yards_gained", "down", "distance", "yards_to_goal", "period",
    "rush", "pass", "pass_attempt", "completion", "target", "sack", "rush_td", "pass_td", "touchdown", "int", "int_td",
    "fumble_vec", "turnover", "change_of_pos_team", "penalty_flag", "penalty_no_play", "penalty_declined",
    "rush_player_id", "rush_player", "rush_yds", "reception_player_id", "reception_player", "reception_yds",
    "completion_player_id", "completion_player", "completion_yds", "incompletion_player_id", "incompletion_player",
    "target_player_id", "target_player", "interception_thrown_player_id", "interception_thrown_player",
    "sack_taken_player_id", "sack_taken_player", "yds_sacked", "touchdown_player_id", "touchdown_player",
    "fumble_player_id", "fumble_player", "fumble_recovered_player_id", "fumble_recovered_player",
    "spread", "over_under", "pos_team_score", "def_pos_team_score", "offense_play", "defense_play",
    "home_team_id", "away_team_id",
]

LOAD_WARNINGS: list[str] = []
# postseason week w -> POSTSEASON_WEEK_OFFSET + w (the regular season runs weeks 0-16)
POSTSEASON_WEEK_OFFSET = 20


class DataUnavailable(RuntimeError):
    """A release file does not exist (404) or could not be downloaded and is not cached."""


def current_season(today: date | None = None) -> int:
    """CFB season year: the calendar year from August on (week 0/1 is late Aug), else the year before."""
    today = today or date.today()
    return today.year if today.month >= 8 else today.year - 1


def url_for(name: str, season: int) -> str:
    if name not in DATASETS:
        raise KeyError(f"unknown sportsdataverse dataset {name!r}; known: {sorted(DATASETS)}")
    tag, tmpl = DATASETS[name]
    return f"{BASE_URL}/{tag}/{tmpl.format(season=season)}"


def cache_path(name: str, season: int) -> Path:
    return CACHE_DIR / f"{name}_{season}.parquet"


def _is_stale(path: Path, season: int, max_age_hours: float) -> bool:
    if not path.exists():
        return True
    if season < current_season():
        return False
    return (time.time() - path.stat().st_mtime) > max_age_hours * 3600.0


def download(name: str, season: int, dest: Path | None = None, timeout: float = 300.0) -> Path:
    """Fetch one release parquet to ``dest`` atomically; validates the parquet magic bytes."""
    require("sportsdataverse")
    url = url_for(name, season)
    dest = dest or cache_path(name, season)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
    except urllib.error.HTTPError as e:
        tmp.unlink(missing_ok=True)
        raise DataUnavailable(f"{url}: HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        tmp.unlink(missing_ok=True)
        raise DataUnavailable(f"{url}: {e}") from e
    with open(tmp, "rb") as f:
        head = f.read(4)
        f.seek(-4, os.SEEK_END)
        tail = f.read(4)
    if head != b"PAR1" or tail != b"PAR1":
        tmp.unlink(missing_ok=True)
        raise DataUnavailable(f"{url}: response is not a parquet file")
    tmp.replace(dest)
    return dest


@lru_cache(maxsize=64)
def _read(path: str, mtime: float, columns: tuple[str, ...] | None) -> pd.DataFrame:
    if columns is None:
        return pd.read_parquet(path)
    import pyarrow.parquet as pq

    have = set(pq.read_schema(path).names)
    return pd.read_parquet(path, columns=[c for c in columns if c in have])


def load_season(name: str, season: int, max_age_hours: float | None = None, refresh: bool = False,
                columns: Iterable[str] | None = None) -> pd.DataFrame:
    """One season of one dataset (cached). ``columns`` projects the read (pbp is wide)."""
    max_age = DEFAULT_MAX_AGE_H if max_age_hours is None else max_age_hours
    path = cache_path(name, season)
    if refresh or _is_stale(path, season, max_age):
        try:
            download(name, season, path)
        except DataUnavailable as e:
            if not path.exists():
                raise
            age_h = (time.time() - path.stat().st_mtime) / 3600.0
            msg = f"sportsdataverse {name} {season}: refresh failed ({e}); using cached copy {age_h:.1f}h old"
            if msg not in LOAD_WARNINGS:
                LOAD_WARNINGS.append(msg)
    cols = tuple(columns) if columns is not None else None
    if name == "pbp" and cols is None:
        cols = tuple(PBP_COLUMNS)
    df = _read(str(path), path.stat().st_mtime, cols).copy()
    return _normalize(df, season)


def load(name: str, seasons: int | Iterable[int], max_age_hours: float | None = None, refresh: bool = False,
         columns: Iterable[str] | None = None, optional: bool = False) -> pd.DataFrame:
    """Concatenate cached seasons. ``optional``: a missing release file yields no rows instead of an error."""
    seasons = [seasons] if isinstance(seasons, int) else list(seasons)
    frames = []
    for s in seasons:
        try:
            frames.append(load_season(name, s, max_age_hours, refresh, columns))
        except DataUnavailable as e:
            if not optional:
                raise
            msg = f"sportsdataverse {name} {s} unavailable: {e}"
            if msg not in LOAD_WARNINGS:
                LOAD_WARNINGS.append(msg)
    frames = [f for f in frames if len(f)]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _normalize(df: pd.DataFrame, season: int) -> pd.DataFrame:
    """Integer season/week; ``season`` filled from the file when a dataset lacks it (pbp uses ``year``)."""
    if "season" not in df.columns or df["season"].isna().any():
        df["season"] = season if "season" not in df.columns else df["season"].fillna(season)
    for col in ("season", "week"):
        if col in df.columns and not pd.api.types.is_integer_dtype(df[col]):
            v = pd.to_numeric(df[col], errors="coerce")
            df[col] = v.astype("int64") if v.notna().all() else v.astype("Int64")
    if "week" in df.columns and "season_type" in df.columns:
        post = df["season_type"].astype(str).str.strip().str.lower().eq("postseason").to_numpy()
        if post.any():
            df.loc[post, "week"] = df.loc[post, "week"] + POSTSEASON_WEEK_OFFSET
    for col in ("game_id", "team_id", "athlete_id", "home_id", "away_id"):
        if col in df.columns and not pd.api.types.is_integer_dtype(df[col]):
            v = pd.to_numeric(df[col], errors="coerce")
            if v.notna().all():
                df[col] = v.astype("int64")
    return df


# --------------------------------------------------------------------------- point-in-time helpers


def week_key(season, week) -> np.ndarray | int:
    """Sortable (season, week) index (postseason weeks are already above the regular season, see ``_normalize``)."""
    return np.asarray(season, dtype=np.int64) * 100 + np.asarray(week, dtype=np.int64)


def week_label(week: int) -> str:
    """'week 7', or 'postseason week 1' for a remapped postseason week."""
    w = int(week)
    return f"postseason week {w - POSTSEASON_WEEK_OFFSET}" if w > POSTSEASON_WEEK_OFFSET else f"week {w}"


def before(df: pd.DataFrame, season: int, week: int, season_col: str = "season", week_col: str = "week",
           min_season: int | None = None) -> pd.DataFrame:
    """Rows strictly before (season, week). The target week itself is always excluded."""
    if df.empty:
        return df
    k = week_key(df[season_col].to_numpy(), pd.to_numeric(df[week_col], errors="coerce").fillna(0).to_numpy())
    mask = k < week_key(season, week)
    if min_season is not None:
        mask &= df[season_col].to_numpy() >= min_season
    return df.loc[mask]


SCORE_COLUMNS = ("home_points", "away_points", "home_winner", "away_winner", "completed", "status",
                 "attendance", "highlights", "home_line_scores", "away_line_scores")


def week_games(schedules: pd.DataFrame, season: int, week: int) -> pd.DataFrame:
    """Target-week schedule rows with every result column dropped (nothing downstream can peek)."""
    s = schedules[(schedules["season"] == season) & (schedules["week"] == week)].copy()
    return s.drop(columns=[c for c in SCORE_COLUMNS if c in s.columns])


def implied_points(home_spread, total):
    """``home_spread`` < 0 means the HOME team is favoured (ESPN ``home_team_spread`` convention).

    Returns (home_implied, away_implied) = ((total - spread)/2, (total + spread)/2).
    """
    s = np.asarray(home_spread, dtype=float)
    t = np.asarray(total, dtype=float)
    return (t - s) / 2.0, (t + s) / 2.0


def game_lines(betting: pd.DataFrame, pbp_lines: pd.DataFrame | None = None) -> pd.DataFrame:
    """One pre-game line per game_id: ``home_spread`` (negative = home favoured) and ``total``.

    ESPN betting rows are preferred; the cfbfastR pbp ``spread``/``over_under`` fill games ESPN
    lacks (checked on 2026 wk 1-7: identical to ``home_team_spread`` on all 510 shared games, same sign).
    """
    out = pd.DataFrame(columns=["game_id", "home_spread", "total", "line_source"])
    if betting is not None and len(betting):
        b = betting.copy()
        b = b[b.get("game_spread_available", True).astype(bool)] if "game_spread_available" in b.columns else b
        spread = pd.to_numeric(b["home_team_spread"], errors="coerce")
        out = pd.DataFrame({"game_id": b["game_id"].astype("int64"), "home_spread": spread,
                            "total": pd.to_numeric(b["over_under"], errors="coerce"),
                            "line_source": "espn:" + b.get("odds_source", pd.Series("odds", index=b.index)).astype(str)})
    if pbp_lines is not None and len(pbp_lines):
        p = pbp_lines.dropna(subset=["spread", "over_under"]).drop_duplicates("game_id")
        extra = pd.DataFrame({"game_id": p["game_id"].astype("int64"), "home_spread": pd.to_numeric(p["spread"]),
                              "total": pd.to_numeric(p["over_under"]), "line_source": "cfbd(pbp)"})
        extra = extra[~extra["game_id"].isin(out["game_id"])]
        out = pd.concat([out, extra], ignore_index=True) if len(out) else extra
    out = out.dropna(subset=["home_spread", "total"])
    return out.drop_duplicates("game_id").reset_index(drop=True)


def kickoff_utc(start_date) -> pd.Timestamp | None:
    """Schedule ``start_date`` (ISO, UTC 'Z') -> aware UTC timestamp."""
    if start_date is None or (isinstance(start_date, float) and np.isnan(start_date)):
        return None
    t = pd.Timestamp(start_date)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
