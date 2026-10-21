"""End-to-end runs on fixture files with fake agents (no network, no model calls)."""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from dfs.io.contest_file import load_contest
from dfs.lastcall import lastcall
from dfs.llm.backend import FakeBackend
from dfs.models import UserRules
from dfs.optimize.ilp import check_legal
from dfs.pipeline import RunConfig, run

FIX = Path(__file__).parent / "fixtures"


def fake(strategist=None, checker=None, news=None, backup=None):
    def research(t):
        return {"game": t.name.split(":", 1)[1], "summary": "fake", "facts": [], "ruled_out": [],
                "doubtful_or_questionable": []}
    return FakeBackend({
        "research": research,
        "strategist": strategist or (lambda t: {"remove_not_playing": [], "cuts": [], "close_calls": [],
                                                "weakest_pick": {"player_key": "", "why": ""}, "preferred_draft": "B"}),
        "checker": checker or (lambda t: {"attacks": []}),
        "backup": backup or (lambda t: {"adjustments": [], "remove": []}),
        "news": news or (lambda t: {"updates": []}),
    })


@pytest.fixture
def cfg(tmp_path):
    return RunConfig(out_dir=tmp_path / "runs", provider="generic", n_sims=3000, learned_blend=None)


def test_run_writes_legal_upload_and_report(cfg):
    res = run(FIX / "dk_salaries_classic.csv", cfg, backend=fake(), log=lambda s: None)
    assert check_legal(res.contest, res.final) == []
    rows = list(csv.reader(open(res.upload_path)))
    assert rows[0] == res.contest.slot_headers and rows[1] == [s.site_id for s in res.final.slots]
    for name in ("contest.json", "baseline.json", "dossiers.json", "projections.json", "report.json", "report.md"):
        assert (res.run_dir / name).exists()
    out = {k for k, p in res.contest.players.items() if p.injury_status == "OUT"}
    assert not (set(res.final.player_keys) & out)
    assert {"blend", "after_strategist", "after_checker", "final"} <= set(res.stage_lineups)


def test_checker_blocker_forces_revision(cfg):
    first = {}

    def checker(t):
        if "attempt" not in first:
            pick = t.prompt.split("- QB: ")[1].split(" (")[0]
            first["attempt"] = pick
            return {"attacks": [{"player_key": pick, "claim": "not_playing", "severity": "blocker",
                                 "fact_ids": ["X.F1"], "argument": "out"}]}
        return {"attacks": []}

    # The checker cites a fact that does not exist, so code must ignore the blocker.
    res = run(FIX / "dk_salaries_classic.csv", cfg, backend=fake(checker=checker), log=lambda s: None)
    assert res.report["decision"]["checker"][0]["passed"] is True
    assert first["attempt"] in res.final.player_keys


def test_strategist_failure_uses_backup(cfg):
    def broken(t):
        raise RuntimeError("model down")
    res = run(FIX / "dk_salaries_classic.csv", cfg, backend=fake(strategist=broken), log=lambda s: None)
    assert res.report["decision"].get("used_backup") is True
    assert check_legal(res.contest, res.final) == []


def test_user_rules_second_lineup(cfg):
    c = load_contest(FIX / "dk_salaries_classic.csv")
    some = sorted(p.name for p in c.players.values() if p.positions == ["WR"] and p.injury_status != "OUT")[0]
    cfg.user_rules = UserRules(lock=[some], max_from_team=2)
    res = run(FIX / "dk_salaries_classic.csv", cfg, backend=fake(), log=lambda s: None)
    assert res.rules_lineup is not None and res.rules_upload_path.exists()
    assert any(c.players[k].name == some for k in res.rules_lineup.player_keys)


def test_entries_file_gets_one_lineup_per_entry(cfg):
    res = run(FIX / "dk_entries_classic.csv", cfg, backend=fake(), log=lambda s: None)
    rows = list(csv.reader(open(res.upload_path)))
    assert rows[0][:4] == ["Entry ID", "Contest Name", "Contest ID", "Entry Fee"]
    assert len(rows) - 1 == len(res.contest.entries)
    locked_id = res.contest.entries[0].current[0]
    assert rows[1][4] == locked_id  # the locked QB stays in the first entry


def test_lastcall_swaps_only_when_a_pick_is_out(cfg):
    res = run(FIX / "dk_salaries_classic.csv", cfg, backend=fake(), log=lambda s: None)
    early = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
    quiet = lastcall(res.run_dir, web=True, now=early, backend=fake(), log=lambda s: None)
    assert quiet["changed"] is False
    victim = res.final.slots[3].player_key
    name = res.contest.players[victim].name
    news = lambda t: {"updates": [{"player": name, "status": "inactive", "text": f"{name} inactive"}]}
    loud = lastcall(res.run_dir, web=True, now=early, backend=fake(news=news), log=lambda s: None)
    assert loud["changed"] is True and victim not in loud["final"].player_keys
    assert check_legal(res.contest, loud["final"]) == []
    assert (res.run_dir / "upload_final.csv").exists()


def test_lastcall_keeps_started_players(cfg):
    res = run(FIX / "dk_salaries_classic.csv", cfg, backend=fake(), log=lambda s: None)
    starts = sorted({p.start_time for p in res.contest.players.values() if p.start_time})
    mid = starts[0] + timedelta(minutes=30)  # early games started, late game not yet
    started_picks = {k for k in res.final.player_keys if res.contest.players[k].start_time <= mid}
    victim = next((k for k in res.final.player_keys if k not in started_picks), None)
    if victim is None:
        pytest.skip("fixture lineup has no late-game player")
    name = res.contest.players[victim].name
    news = lambda t: {"updates": [{"player": name, "status": "out", "text": "out"}]}
    out = lastcall(res.run_dir, web=True, now=mid, backend=fake(news=news), log=lambda s: None)
    assert started_picks <= set(out["final"].player_keys)
    assert victim not in out["final"].player_keys


def test_safety_upload_keeps_locked_cells(cfg):
    res = run(FIX / "dk_entries_classic.csv", cfg, backend=fake(), log=lambda s: None)
    rows = list(csv.reader(open(res.run_dir / "upload_safety.csv")))
    assert len(rows) - 1 == len(res.contest.entries)
    assert rows[1][4] == res.contest.entries[0].current[0]  # locked QB kept in the safety file too


def test_run_stops_cleanly_when_every_game_started(cfg):
    from dfs.pipeline import SlateStarted
    c = load_contest(FIX / "dk_salaries_classic.csv")
    late = max(p.start_time for p in c.players.values() if p.start_time) + timedelta(hours=1)
    be = fake()
    with pytest.raises(SlateStarted):
        run(FIX / "dk_salaries_classic.csv", cfg, backend=be, log=lambda s: None, now=late)
    assert be.calls == []  # nothing spent


def test_fd_kickoffs_come_from_the_schedule():
    """FD files have no kickoff times; the NFL provider supplies them (ET -> UTC, DST-aware)."""
    from types import SimpleNamespace

    import pandas as pd

    from dfs.data.teams import canonicalize
    from dfs.projections.nfl_provider import kickoffs

    c = load_contest(FIX / "fd_players_classic.csv")
    assert all(p.start_time is None for p in c.players.values())
    rows = []
    for i, g in enumerate(c.games):
        a, h = (canonicalize(x, strict=False) for x in g.split("@"))  # schedule uses nflverse codes (LA, not LAR)
        rows.append({"season": 2026, "week": 8, "gameday": "2026-11-01" if i else "2026-10-25",
                     "gametime": "13:00", "away_team": a, "home_team": h})
    ko = kickoffs(c, SimpleNamespace(schedules=pd.DataFrame(rows)), 2026, 8)
    assert set(ko) == set(c.games)
    first = ko[c.games[0]]
    assert first == datetime(2026, 10, 25, 17, 0, tzinfo=timezone.utc)  # 1pm EDT
    assert ko[c.games[1]] == datetime(2026, 11, 1, 18, 0, tzinfo=timezone.utc)  # 1pm EST after the clock change
