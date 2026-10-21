"""Core data contracts shared by every stage of the engine.

Everything that crosses a stage boundary is one of these models, so each stage can be
run, cached, replayed and tested on its own. Points are always in the contest's own
scoring (the site/sport scoring table named by ``ContestRules.scoring``).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class Site(str, Enum):
    DRAFTKINGS = "draftkings"
    FANDUEL = "fanduel"


class ContestKind(str, Enum):
    CASH = "cash"  # 50/50, double-up, head-to-head: beat roughly the median
    GPP = "gpp"  # tournaments: finish near the top of the field


# --------------------------------------------------------------------------- contest


class Slot(BaseModel):
    """One roster position in lineup order. Duplicates are separate slots (RB, RB)."""

    name: str  # header label as it appears in the entries/upload file: QB, FLEX, CPT, MVP...
    eligible: list[str]  # player positions that may fill it, e.g. ["RB", "WR", "TE"]
    points_multiplier: float = 1.0  # CPT/MVP 1.5
    salary_multiplier: float = 1.0  # informational; DK showdown CPT rows carry their own salary


class ContestRules(BaseModel):
    """Facts about a contest type that are not in the CSV: the saved rules registry."""

    site: Site
    sport: str  # nfl, cfb, nba, mlb, nhl, pga, mma, nascar, soccer, ...
    game_type: str  # classic, showdown, single_game, ...
    salary_cap: int
    slots: list[Slot]
    max_per_team: int | None = None
    max_hitters_per_team: int | None = None  # DK MLB: at most 5 hitters (non-pitchers) from one team
    min_teams: int | None = None
    min_games: int | None = None
    # A multiplied slot (CPT/MVP) appears as its own CSV row with its own ID and salary (DK showdown).
    multiplier_rows: bool = False
    scoring: str  # key of the scoring table, e.g. "draftkings.nfl"
    late_swap: bool = False
    # FD single game prices the MVP slot from its own column ("MVP 1.5x Salary").
    salary_column_by_slot: dict[str, str] = Field(default_factory=dict)
    confidence: str = "medium"  # how well the registry entry is verified: high | medium | low
    notes: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    verified_on: str | None = None

    @property
    def roster_size(self) -> int:
        return len(self.slots)


class Player(BaseModel):
    """A real participant on the slate (one per person/team, however many CSV rows)."""

    key: str  # stable internal key, see dfs.io.common.player_key
    name: str
    team: str
    opponent: str | None = None
    positions: list[str]
    game: str | None = None  # canonical "AWAY@HOME"
    start_time: datetime | None = None
    site_avg: float | None = None  # site FPPG / AvgPointsPerGame (never shown to the blinded forecaster)
    injury_status: str | None = None  # site injury indicator (Q, D, O, IR, ...)
    injury_detail: str | None = None
    data_id: str | None = None  # id in the stats source (e.g. nflverse gsis_id) once matched


class Candidate(BaseModel):
    """One selectable CSV row: a player, the slots that row may fill, its salary and upload ID."""

    player_key: str
    site_id: str  # exactly what the upload file needs in the slot cell
    salary: int
    eligible_slots: list[str]  # slot *names* this row may fill (e.g. ["CPT"] or ["RB", "FLEX"])
    display: str | None = None  # "Name (ID)" as the site prints it


class Entry(BaseModel):
    """One already-entered lineup row from a DK/FD entries file (to be edited and re-uploaded)."""

    entry_id: str
    contest_id: str
    contest_name: str
    entry_fee: str | None = None
    current: list[str | None] = Field(default_factory=list)  # site ids per slot, if pre-filled
    locked: list[bool] = Field(default_factory=list)  # DK marks started players "(LOCKED)"


class Contest(BaseModel):
    rules: ContestRules
    players: dict[str, Player]
    candidates: list[Candidate]
    entries: list[Entry] = Field(default_factory=list)
    slot_headers: list[str]  # slot labels exactly as the file writes them (same order/length as rules.slots)
    source_file: str
    file_kind: str  # "dk_salaries" | "dk_entries" | "fd_players" | "fd_entries"
    kind: ContestKind = ContestKind.GPP
    field_size: int | None = None
    games: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)  # parser/registry disagreements the user must see

    def candidates_for(self, player_key: str) -> list[Candidate]:
        return [c for c in self.candidates if c.player_key == player_key]


# --------------------------------------------------------------------------- research


class Fact(BaseModel):
    """One sourced fact from research. Facts, never opinions, cross the research boundary."""

    id: str = ""  # run-unique, e.g. "BUF@MIA.F3"; agents cite these
    subject: str  # player name, team abbreviation, or "game"
    team: str | None = None
    kind: str  # injury | status | role | usage | depth_chart | matchup | weather | narrative | line
    text: str
    source_url: str | None = None
    as_of: str | None = None
    confidence: str = "medium"  # high | medium | low
    status: str | None = None  # availability, when the fact states it: out | inactive | injured_reserve | ...


class GameDossier(BaseModel):
    game: str  # "AWAY@HOME"
    summary: str = ""
    facts: list[Fact] = Field(default_factory=list)
    ruled_out: list[str] = Field(default_factory=list)  # player names confirmed not playing
    doubtful_or_questionable: list[str] = Field(default_factory=list)
    spread_home: float | None = None  # home team spread (negative = home favoured)
    total: float | None = None
    weather: str | None = None


# --------------------------------------------------------------------------- projections


class Projection(BaseModel):
    """A player's fantasy-point distribution under the contest's scoring (1x slot)."""

    player_key: str
    mean: float
    p10: float
    p50: float
    p90: float
    p_zero: float = 0.0  # probability of a non-appearance / zero (inactive, early exit)
    source: str = "baseline"  # baseline | forecast | blend | site_avg
    components: dict[str, float] = Field(default_factory=dict)
    notes: str | None = None


class Forecast(BaseModel):
    """Blinded forecaster output for one player: low/likely/high are 10th/50th/90th percentiles."""

    player_key: str
    low: float
    likely: float
    high: float
    p_dnp: float = 0.0
    rationale: str = ""


# --------------------------------------------------------------------------- lineups


class LineupSlot(BaseModel):
    slot: str
    player_key: str
    site_id: str
    salary: int
    points_multiplier: float = 1.0


class Lineup(BaseModel):
    slots: list[LineupSlot]
    label: str = ""
    salary: int = 0
    mean: float = 0.0
    metrics: dict[str, float] = Field(default_factory=dict)

    @property
    def player_keys(self) -> list[str]:
        return [s.player_key for s in self.slots]


class UserRules(BaseModel):
    """The user's own pick rules, solved as a second lineup. Unknown keys are an error (a typo must not
    be silently ignored)."""

    model_config = ConfigDict(extra="forbid")

    lock: list[str] = Field(default_factory=list)  # player names/keys that must be in
    exclude: list[str] = Field(default_factory=list)
    exclude_teams: list[str] = Field(default_factory=list)
    exclude_games: list[str] = Field(default_factory=list)
    max_from_team: int | None = None
    min_salary: int | None = None
    stack: list[dict] = Field(default_factory=list)  # e.g. {"team": "BUF", "positions": ["QB","WR"], "min": 2}
