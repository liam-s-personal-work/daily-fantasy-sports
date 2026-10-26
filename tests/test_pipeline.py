"""End-to-end runs on fixture files with fake agents (no network, no model calls)."""

from __future__ import annotations

import csv
import dataclasses
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from dfs.io.contest_file import load_contest
from dfs.lastcall import lastcall
from dfs.llm.backend import FakeBackend
from dfs.models import UserRules
from dfs.optimize.ilp import check_legal
from dfs.pipeline import RunConfig
from dfs.pipeline import run as _run

FIXTURE_MORNING = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)  # 8 AM ET on the fixtures' game day


def run(path, cfg, backend=None, log=print, now=FIXTURE_MORNING):
    """The pipeline as of a fixed time before the fixtures' kickoffs, so the tests never depend on today's date."""
    return _run(path, cfg, backend=backend, log=log, now=now)

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


def _rows(path):
    """Upload rows with DK's "Name (ID)" cells reduced to the ID."""
    import re
    return [[(m.group(1) if (m := re.fullmatch(r".*\((\d+)\)", c.strip())) else c) for c in r]
            for r in csv.reader(open(path))]


@pytest.fixture
def cfg(tmp_path):
    # allow_lookahead: the fake backend cannot see the future, and the fixed clock is in the past once the date passes
    return RunConfig(out_dir=tmp_path / "runs", provider="generic", n_sims=3000, learned_blend=None,
                     allow_lookahead=True)


def test_run_writes_legal_upload_and_report(cfg):
    res = run(FIX / "dk_salaries_classic.csv", cfg, backend=fake(), log=lambda s: None)
    assert check_legal(res.contest, res.final) == []
    rows = _rows(res.upload_path)
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
    rows = _rows(res.upload_path)
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
    news = lambda t: {"updates": [{"player": name, "status": "inactive", "text": f"{name} inactive", "source_url": "https://www.nfl.com/news/inactives"}]}
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
    news = lambda t: {"updates": [{"player": name, "status": "out", "text": "out", "source_url": "https://www.nfl.com/news/inactives"}]}
    out = lastcall(res.run_dir, web=True, now=mid, backend=fake(news=news), log=lambda s: None)
    assert started_picks <= set(out["final"].player_keys)
    assert victim not in out["final"].player_keys


def test_safety_upload_keeps_locked_cells(cfg):
    res = run(FIX / "dk_entries_classic.csv", cfg, backend=fake(), log=lambda s: None)
    rows = _rows(res.run_dir / "upload_safety.csv")
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


def test_lastcall_covers_every_entry(cfg):
    res = run(FIX / "dk_entries_classic.csv", cfg, backend=fake(), log=lambda s: None)
    import json
    lus = json.loads((res.run_dir / "report.json").read_text())["lineups"]
    assert len(lus) == 3
    final_keys = set(res.final.player_keys)
    others = [i for i, lu in enumerate(lus) if {s["player_key"] for s in lu["slots"]} != final_keys]
    assert others, "fixture should give at least one entry its own lineup"
    i = others[-1]
    mine = [s["player_key"] for s in lus[i]["slots"]]
    locked_qb = res.contest.entries[0].current[0]
    victim = next(k for k in mine if k not in final_keys and not any(
        x.site_id == locked_qb for x in res.contest.candidates if x.player_key == k))
    name = res.contest.players[victim].name
    news = lambda t: {"updates": [{"player": name, "status": "out", "text": f"{name} ruled out", "source_url": "https://www.nfl.com/news/inactives"}]}
    early = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
    out = lastcall(res.run_dir, web=True, now=early, backend=fake(news=news), log=lambda s: None)
    rows = _rows(res.run_dir / "upload_final.csv")
    assert len(rows) - 1 == 3  # every entry is in the final file
    assert out["entries"]["repaired"] >= 1 and out["entries"]["could_not_repair"] == []
    assert rows[1][4] == locked_qb  # entry 1's locked QB is untouched
    by_id = {x.site_id: x.player_key for x in res.contest.candidates}
    new_i = [by_id[x] for x in rows[i + 1][4:13]]
    assert victim not in new_i
    assert len(set(new_i) & set(mine)) >= len(mine) - 2  # a repair, not a rebuild


def test_lastcall_reforecast_keeps_the_blind(cfg):
    c = load_contest(FIX / "dk_salaries_classic.csv")

    def forecast(t):
        keys = [k for k in c.players if k in t.prompt]
        return {"game": t.name.split(":", 1)[1], "players": [
            {"player_key": k, "low": 2.0, "likely": 9.0, "high": 20.0, "p_dnp": 0.02, "rationale": "x"} for k in keys]}

    def research(t):  # every game gets one leaking fact and one clean fact
        g = t.name.split(":", 1)[1]
        p = next(x for x in c.players.values() if x.game == g and x.positions[0] == "WR")
        facts = [{"subject": p.name, "team": p.team, "kind": "role", "text": f"{p.name} at a $7,800 salary is chalk",
                  "source_url": "https://www.nfl.com/a", "as_of": "2026-10-20", "confidence": "high"},
                 {"subject": p.name, "team": p.team, "kind": "role", "text": f"{p.name} named the slot starter",
                  "source_url": "https://www.nfl.com/b", "as_of": "2026-10-20", "confidence": "high"}]
        return {"game": g, "summary": "s", "facts": facts, "ruled_out": [], "doubtful_or_questionable": []}

    be = fake()
    be.handlers.update({"research": research, "forecast": forecast})
    res = run(FIX / "dk_salaries_classic.csv", cfg, backend=be, log=lambda s: None)
    assert res.report and (res.run_dir / "forecasts.json").exists()
    pick = res.final.slots[3].player_key
    name = c.players[pick].name
    news = lambda t: {"updates": [{"player": name, "status": "questionable", "text": f"{name} questionable, hamstring",
                                   "source_url": "https://www.nfl.com/c"}]}
    lc_be = fake(news=news)
    lc_be.handlers["forecast"] = forecast
    lastcall(res.run_dir, web=True, now=datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc), backend=lc_be,
             log=lambda s: None)
    prompts = [t.prompt for t in lc_be.calls if t.stage == "forecast"]
    assert prompts, "the questionable pick should trigger a targeted re-forecast"
    assert "$7,800" in (res.run_dir / "dossiers.json").read_text()  # the leak is in the research...
    assert not any("$7,800" in x or "salary" in x for x in prompts)  # ...but never reaches the forecaster
    assert any("named the slot starter" in x for x in prompts)


ET_ = timezone(timedelta(hours=-4))  # EDT on 2026-10-25


def _cells(path):
    rows = _rows(path)
    return {r[0]: r[4:13] for r in rows[1:]}


def test_late_swap_run_keeps_started_cells_in_every_entry(cfg):
    now = datetime(2026, 10, 25, 13, 30, tzinfo=ET_)  # the 1 PM games are on, SEA@LAR (4:25) is not
    res = run(FIX / "dk_entries_classic.csv", cfg, backend=fake(), log=lambda s: None, now=now)
    c = res.contest
    live = {x.site_id for x in c.candidates if c.players[x.player_key].start_time <= now}
    for name in ("upload.csv", "upload_safety.csv"):
        new = _cells(res.run_dir / name)
        for e in c.entries:
            if e.entry_id not in new:
                continue
            for was, now_id in zip(e.current, new[e.entry_id]):
                if was in live:
                    assert now_id == was, (name, e.entry_id)  # a started player is never moved or dropped
                else:
                    assert now_id not in live or now_id == was  # and never added


def test_late_swap_with_one_game_left_builds_around_locks(cfg, tmp_path):
    text = (FIX / "dk_entries_classic.csv").read_text(encoding="utf-8-sig")
    text = text.replace("BUF@MIA 10/25/2026 01:00PM ET", "In Progress").replace("KC@LV 10/25/2026 01:00PM ET", "Final")
    p = tmp_path / "DKEntries_live.csv"
    p.write_text(text, encoding="utf-8")
    now = datetime(2026, 10, 25, 15, 0, tzinfo=ET_)
    res = run(p, cfg, backend=fake(), log=lambda s: None, now=now)  # min 2 games but only SEA@LAR is open
    assert check_legal(res.contest, res.final) == []
    assert res.report["decision"].get("built_around_locks")
    new = _cells(res.upload_path)
    for e in res.contest.entries:
        if e.entry_id in new:
            for was, now_id in zip(e.current, new[e.entry_id]):
                p_was = next((x for x in res.contest.candidates if x.site_id == was), None)
                if p_was and res.contest.players[p_was.player_key].game != "SEA@LAR":
                    assert now_id == was


def test_lastcall_keeps_a_started_pick_that_news_calls_out(cfg):
    res = run(FIX / "dk_salaries_classic.csv", cfg, backend=fake(), log=lambda s: None)
    starts = sorted({p.start_time for p in res.contest.players.values() if p.start_time})
    mid = starts[0] + timedelta(minutes=30)
    started_pick = next((k for k in res.final.player_keys if res.contest.players[k].start_time <= mid), None)
    if started_pick is None:
        pytest.skip("fixture lineup has no early-game player")
    name = res.contest.players[started_pick].name
    news = lambda t: {"updates": [{"player": name, "status": "out", "text": "left the game, ruled out", "source_url": "https://www.nfl.com/news/inactives"}]}
    out = lastcall(res.run_dir, web=True, now=mid, backend=fake(news=news), log=lambda s: None)
    assert started_pick in out["final"].player_keys and started_pick in out["out_but_locked"]
    assert check_legal(res.contest, out["final"]) == []


def test_mixed_cash_and_tournament_entries_get_their_own_objective(cfg):
    from dfs.io.contest_file import contest_kind
    res = run(FIX / "dk_entries_classic.csv", cfg, backend=fake(), log=lambda s: None)
    kinds = {contest_kind([e.contest_name]).value for e in res.contest.entries}
    assert kinds == {"cash", "gpp"}  # the fixture mixes a Double Up with a tournament
    assert res.report["decision"]["entries"]["mixed_contest_kinds"] is True
    for lu in res.report["lineups"]:
        assert check_legal(res.contest, lu) == []


def test_checker_failures_after_two_revisions_stay_excluded(cfg):
    import re as _re
    c = load_contest(FIX / "dk_salaries_classic.csv")

    def research(t):  # every QB is reported ruled out by an official source
        g = t.name.split(":", 1)[1]
        facts = [{"subject": p.name, "team": p.team, "kind": "status", "status": "out", "text": f"{p.name} ruled out",
                  "source_url": "https://www.nfl.com/injuries", "as_of": "2026-10-20", "confidence": "high"}
                 for p in c.players.values() if p.game == g and p.positions == ["QB"]]
        return {"game": g, "summary": "s", "facts": facts, "ruled_out": [], "doubtful_or_questionable": []}

    failed = []

    def checker(t):  # fail the lineup's QB, citing the fact about him
        qb_key = _re.search(r"- QB: (.+?) \(", t.prompt).group(1)
        name = c.players[qb_key].name
        fid = _re.search(r"\[(\S+)\] \(status, high\) " + _re.escape(name) + ":", t.prompt).group(1)
        failed.append(qb_key)
        return {"attacks": [{"player_key": qb_key, "claim": "not_playing", "severity": "blocker", "fact_ids": [fid],
                             "argument": "ruled out"}]}

    be = fake(checker=checker)
    be.handlers["research"] = research
    res = run(FIX / "dk_salaries_classic.csv", cfg, backend=be, log=lambda s: None)
    assert len(failed) == 3 and res.report["decision"].get("used_backup") is True
    assert not set(failed) & set(res.final.player_keys)  # the third verified failure is excluded too


def test_lastcall_after_kickoff_checks_against_the_uploaded_lineups(cfg):
    morning = datetime(2026, 10, 25, 8, 0, tzinfo=ET_)
    res = run(FIX / "dk_entries_classic.csv", cfg, backend=fake(), log=lambda s: None, now=morning)
    uploaded = _cells(res.upload_path)
    after = datetime(2026, 10, 25, 13, 5, tzinfo=ET_)  # 1 PM games under way; our upload holds started players
    out = lastcall(res.run_dir, web=True, now=after, backend=fake(), log=lambda s: None)
    assert out["changed"] in (True, False)
    final = _cells(res.run_dir / "upload_final.csv")
    c = res.contest
    live = {x.site_id for x in c.candidates if c.players[x.player_key].start_time <= after}
    for eid, cells in uploaded.items():
        for was, now_id in zip(cells, final[eid]):
            if was in live:
                assert now_id == was  # started cells of the uploaded lineup stay


def test_backtest_command_passes_its_options(monkeypatch):
    import dfs.backtest as bt
    from dfs.cli import main
    seen = {}
    monkeypatch.setattr(bt, "main", lambda argv, prog=None: seen.setdefault("argv", list(argv)) and 0)
    main(["backtest", "--season", "2026", "--weeks", "3-6"])
    assert seen["argv"] == ["--season", "2026", "--weeks", "3-6"]


def test_rules_lineup_failure_is_visible(cfg, capsys):
    from dfs.cli import main
    rules = cfg.out_dir.parent / "rules.yaml"
    rules.write_text("lock: [Nobody Atall]\n")
    rc = main(["run", str(FIX / "dk_salaries_classic.csv"), "--no-web", "--no-forecast", "--replay", "--provider", "generic",
               "--rules", str(rules), "--out", str(cfg.out_dir), "--now", "2026-10-25T12:00:00+00:00"])
    out = capsys.readouterr().out
    assert rc == 0 and "WARNING: no lineup with your rules" in out and "Nobody Atall" in out
    report = next(cfg.out_dir.glob("*/report.md")).read_text()
    assert "your rules lineup was not built" in report


def test_rules_lineup_keeps_the_first_entrys_locks(cfg):
    some = "Test Sea WR2"
    cfg.user_rules = UserRules(lock=[some])
    res = run(FIX / "dk_entries_classic.csv", cfg, backend=fake(), log=lambda s: None)
    assert res.rules_upload_path is not None
    rows = _rows(res.rules_upload_path)
    assert rows[1][4] == res.contest.entries[0].current[0]  # the first entry's LOCKED QB


def test_lastcall_with_a_fresh_entries_file_starts_from_the_site(cfg, tmp_path):
    morning = datetime(2026, 10, 25, 8, 0, tzinfo=ET_)
    res = run(FIX / "dk_entries_classic.csv", cfg, backend=fake(), log=lambda s: None, now=morning)
    # the user never uploaded our lineups: the site still holds the original ones, and DK marks started cells
    c = res.contest
    after = datetime(2026, 10, 25, 13, 5, tzinfo=ET_)
    live = {x.site_id for x in c.candidates if c.players[x.player_key].start_time <= after}
    text = (FIX / "dk_entries_classic.csv").read_text(encoding="utf-8-sig")
    for e in c.entries:
        for sid in e.current:
            if sid in live:
                text = text.replace(f"({sid}),", f"({sid}) (LOCKED),").replace(f"({sid}) (LOCKED) (LOCKED)", f"({sid}) (LOCKED)")
    fresh = tmp_path / "DKEntries_fresh.csv"
    fresh.write_text(text, encoding="utf-8")
    out = lastcall(res.run_dir, web=True, now=after, backend=fake(), log=lambda s: None, entries_file=fresh)
    final = _cells(res.run_dir / "upload_final.csv")
    for e in c.entries:
        if e.entry_id not in final:  # an empty reservation with one game left cannot be filled legally
            assert not any(e.current) and e.entry_id in out["entries"]["reservations_left_empty"]
            continue
        for was, now_id in zip(e.current, final[e.entry_id]):
            if was in live:
                assert now_id == was  # the site's started cells are kept, whatever the run had planned
    assert out["entries"]["could_not_repair"] == []
    assert len(final) >= 2


def test_backtest_usage_and_errors(capsys):
    from dfs.cli import main
    with pytest.raises(SystemExit):
        main(["backtest", "--help"])
    assert "usage: dfs backtest" in capsys.readouterr().out
    assert main(["backtest", "--params", "/nonexistent/params.json", "--weeks", "6"]) == 2
    assert "dfs backtest:" in capsys.readouterr().err


def _strategist_removes(name):
    def research(t):
        g = t.name.split(":", 1)[1]
        c = load_contest(FIX / "dk_salaries_classic.csv")
        facts = [{"subject": p.name, "team": p.team, "kind": "status", "status": "out", "text": f"{p.name} ruled out",
                  "source_url": "https://www.nfl.com/injuries", "as_of": "2026-10-24", "confidence": "high"}
                 for p in c.players.values() if p.game == g and p.name == name]
        return {"game": g, "summary": "s", "facts": facts, "ruled_out": [], "doubtful_or_questionable": []}

    def strategist(t):
        import re as _re
        fid = _re.search(r"\[(\S+)\] \(status, high\) " + _re.escape(name) + ":", t.prompt)
        key = next(k for k, p in load_contest(FIX / "dk_salaries_classic.csv").players.items() if p.name == name)
        return {"remove_not_playing": [{"player_key": key, "fact_ids": [fid.group(1)] if fid else []}], "cuts": [],
                "close_calls": [], "weakest_pick": {"player_key": "", "why": ""}, "preferred_draft": "B"}
    return research, strategist


def test_lastcall_never_readds_a_player_the_run_removed(cfg):
    first = run(FIX / "dk_salaries_classic.csv", cfg, backend=fake(), log=lambda s: None)
    star = max(first.final.player_keys, key=lambda k: first.projections[k].mean)  # the run's best pick
    name = first.contest.players[star].name
    research, strategist = _strategist_removes(name)
    be = fake(strategist=strategist)
    be.handlers["research"] = research
    cfg2 = dataclasses.replace(cfg, out_dir=cfg.out_dir.parent / "runs2")  # fresh model cache
    res = run(FIX / "dk_salaries_classic.csv", cfg2, backend=be, log=lambda s: None)
    assert star in res.report["decision"]["excluded"] and star not in res.final.player_keys
    out = lastcall(res.run_dir, web=False, now=datetime(2026, 10, 25, 12, 30, tzinfo=timezone.utc), backend=fake(),
                   log=lambda s: None, threshold=-1.0)  # threshold -1: take any new lineup
    assert star not in out["final"].player_keys


def test_lastcall_fresh_file_before_kickoff_keeps_the_plan_and_survives_a_missing_entry(cfg, tmp_path):
    res = run(FIX / "dk_entries_classic.csv", cfg, backend=fake(), log=lambda s: None)
    planned = _cells(res.upload_path)
    lines = (FIX / "dk_entries_classic.csv").read_text(encoding="utf-8-sig").splitlines()
    fresh = tmp_path / "DKEntries_fresh.csv"  # entry 5300000002 withdrawn; 5300000003 still an empty reservation
    fresh.write_text("\n".join(",".join([""] * 4 + l.split(",")[4:]) if l.startswith("5300000002,") else l
                               for l in lines) + "\n", encoding="utf-8")
    out = lastcall(res.run_dir, web=False, now=datetime(2026, 10, 25, 11, 0, tzinfo=timezone.utc), backend=fake(),
                   log=lambda s: None, entries_file=fresh)
    final = _cells(res.run_dir / "upload_final.csv")
    assert "5300000002" not in final and any("left out" in w for w in out["warnings"])
    assert final["5300000003"] == planned["5300000003"]  # before kickoff the planned lineup is entered as is


def test_lastcall_entries_on_a_salary_run_is_reported(cfg):
    res = run(FIX / "dk_salaries_classic.csv", cfg, backend=fake(), log=lambda s: None)
    out = lastcall(res.run_dir, web=False, now=datetime(2026, 10, 25, 12, 30, tzinfo=timezone.utc), backend=fake(),
                   log=lambda s: None, entries_file=FIX / "dk_entries_classic.csv")
    assert any("salary file" in w for w in out["warnings"])


def test_lastcall_summary_reads_named_cells(cfg, capsys):
    from dfs.cli import main
    res = run(FIX / "dk_entries_classic.csv", cfg, backend=fake(), log=lambda s: None)
    assert "(" in list(csv.reader(open(res.upload_path)))[1][5]  # DK cells carry names
    main(["lastcall", str(res.run_dir), "--no-web", "--now", "2026-10-25T12:30:00+00:00"])
    out = capsys.readouterr().out
    assert "slot(s) changed" not in out and "unchanged" in out


def test_run_writes_a_readable_explainer_next_to_the_upload(cfg):
    res = run(FIX / "dk_salaries_classic.csv", cfg, backend=fake(), log=lambda s: None)
    rows = list(csv.reader(open(res.run_dir / "lineup_explained.csv")))
    from dfs.explain import HEADER
    assert rows[0] == HEADER and len(rows) == 1 + len(res.final.slots)
    upload = list(csv.reader(open(res.upload_path)))[1]
    # the same players in slot order (the upload keeps DK's exact text, e.g. "Dolphins  (id)" with two spaces)
    assert [" ".join(r[1].split()) for r in rows[1:]] == [" ".join(c.split()) for c in upload]
    for r, s in zip(rows[1:], res.final.slots):
        pr = res.projections[s.player_key]
        assert r[5] == f"{pr.mean:.1f}" and r[6] == f"{pr.p10:.0f}-{pr.p90:.0f}" and r[8]  # mean, range, reasoning


def test_explain_command_and_last_call_explainer(cfg, capsys):
    from dfs.cli import main
    res = run(FIX / "dk_entries_classic.csv", cfg, backend=fake(), log=lambda s: None)
    (res.run_dir / "lineup_explained.csv").unlink()
    assert main(["explain", str(res.run_dir)]) == 0 and (res.run_dir / "lineup_explained.csv").exists()
    lastcall(res.run_dir, web=False, now=datetime(2026, 10, 25, 12, 30, tzinfo=timezone.utc), backend=fake(),
             log=lambda s: None)
    rows = list(csv.reader(open(res.run_dir / "lineup_explained_final.csv")))
    assert rows[0][0] == "Slot" and sum(1 for r in rows[1:] if r[0]) == len(res.contest.entries) * 9


def test_lastcall_flags_a_swappable_pick_the_news_says_nothing_about(cfg):
    res = run(FIX / "dk_salaries_classic.csv", cfg, backend=fake(), log=lambda s: None)
    out = lastcall(res.run_dir, web=True, now=datetime(2026, 10, 25, 12, 30, tzinfo=timezone.utc),
                   backend=fake(news=lambda t: {"updates": []}), log=lambda s: None)
    assert any("no news came back" in w for w in out["warnings"])


def test_points_objective_picks_the_highest_expected_score(cfg):
    pts = dataclasses.replace(cfg, objective="points", out_dir=cfg.out_dir.parent / "runs_pts")
    res = run(FIX / "dk_salaries_classic.csv", pts, backend=fake(), log=lambda s: None)
    from dfs.optimize.ilp import solve
    best = solve(res.contest, {k: p.mean for k, p in res.projections.items()})
    exp = sum(res.projections[k].mean for k in res.final.player_keys)
    assert exp == pytest.approx(sum(res.projections[k].mean for k in best.player_keys), abs=0.05)  # mean-optimal
    assert "expected points" in (res.run_dir / "report.md").read_text()
    out = lastcall(res.run_dir, web=False, now=datetime(2026, 10, 25, 12, 30, tzinfo=timezone.utc), backend=fake(),
                   log=lambda s: None)
    assert out["metric"] == "mean" and set(out["final"].player_keys) == set(res.final.player_keys)


def test_points_objective_entries_are_the_best_lineups_that_differ_by_four_players(cfg):
    import numpy as np
    from dfs import strategy
    from dfs.optimize.ilp import solve
    res = run(FIX / "dk_salaries_classic.csv", cfg, backend=fake(), log=lambda s: None)
    c, proj = res.contest, res.projections
    lus = strategy.best_lineups(c, proj, [], np.zeros((0, 0)), None, kind=c.kind, target_top=0.023, n=6,
                                objective="points")["lineups"]
    assert len(lus) == 6 and lus[0].mean == pytest.approx(solve(c, {k: p.mean for k, p in proj.items()}).mean, abs=0.01)
    for i, lu in enumerate(lus):
        assert not check_legal(c, lu) and lu.mean <= lus[0].mean + 1e-6
        assert all(len(set(lu.player_keys) - set(prev.player_keys)) >= strategy.PORTFOLIO_MIN_DIFF for prev in lus[:i])
    # three open entries in one tournament: the file's portfolio follows the same rule
    text = (FIX / "dk_entries_classic.csv").read_text().replace(" (LOCKED)", "").replace("NFL $1K Double Up",
                                                                                       "NFL $5K Test Shot [20 Entry Max]")
    path = cfg.out_dir.parent / "DKEntries_open.csv"
    path.write_text(text)
    pts = dataclasses.replace(cfg, objective="points", out_dir=cfg.out_dir.parent / "runs_pts_multi")
    multi = run(path, pts, backend=fake(), log=lambda s: None)
    ids = [set(v) for v in _cells(multi.upload_path).values()]
    assert len(ids) == 3 and all(len(a - b) >= strategy.PORTFOLIO_MIN_DIFF for i, a in enumerate(ids) for b in ids[:i])


def test_every_pool_lineup_states_its_projected_mean(cfg):
    from dfs import strategy
    c = load_contest(FIX / "dk_salaries_classic.csv")
    res = run(FIX / "dk_salaries_classic.csv", cfg, backend=fake(), log=lambda s: None)
    proj = res.projections
    fm = strategy.field_for(c, proj, kind=c.kind, seed=3)
    pool, _ = strategy.candidates(c, proj, base=strategy._base(set(), None, None, c), fm=fm, seed=3)
    for lu in pool:
        assert lu.mean == pytest.approx(sum(proj[s.player_key].mean * s.points_multiplier for s in lu.slots), abs=0.01)
