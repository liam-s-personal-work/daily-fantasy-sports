"""Agent-stage plumbing with a fake backend: parsing, citation checks, code verdicts, blending, grading."""

from __future__ import annotations

import asyncio
import json

import pytest

from dfs.blend import blend, forecast_weight
from dfs.distributions import mixture_mean
from dfs.grade import grade_lineup, update_blend
from dfs.llm.backend import AgentResult, AgentTask, FakeBackend, Runner
from dfs.models import Fact, Forecast, GameDossier, Projection
from dfs.optimize.ilp import solve
from dfs.providers import GenericProvider
from dfs.sources import blind_text, host_blocked, require, SourceNotAllowed
from dfs.stages import agents


def _dossier(contest, game, facts, out=()):
    return GameDossier(game=game, summary="s", facts=facts, ruled_out=list(out))


def test_runner_caches_and_enforces_budget(tmp_path):
    calls = []

    def h(task):
        calls.append(task.name)
        return {"ok": 1}

    r = Runner(FakeBackend({"x": h}), cache_dir=tmp_path, budget_usd=1.0)
    t = AgentTask(stage="x", name="a", system="s", prompt="p", schema={"type": "object"})
    out = asyncio.run(r.run_all([t, t]))
    assert all(o.ok for o in out)
    again = asyncio.run(r.run(t))
    assert again.cached and calls.count("a") <= 2
    r.spent_usd = 5.0
    t2 = AgentTask(stage="x", name="b", system="s", prompt="other", schema={"type": "object"})
    assert asyncio.run(r.run(t2)).error == "run budget exhausted"


def test_runner_replay_only_never_calls_backend(tmp_path):
    fb = FakeBackend({"x": lambda t: {"a": 1}})
    r = Runner(fb, cache_dir=tmp_path, replay_only=True)
    res = asyncio.run(r.run(AgentTask(stage="x", name="a", system="s", prompt="p", schema={})))
    assert not res.ok and not fb.calls


def test_parse_research_assigns_ids_and_drops_blocked_sources():
    res = AgentResult(name="research:BUF@MIA", ok=True, output={
        "game": "BUF@MIA", "summary": "x", "ruled_out": ["Some Guy"], "doubtful_or_questionable": [],
        "facts": [{"id": "1", "subject": "Some Guy", "kind": "injury", "text": "ruled out", "confidence": "high",
                   "source_url": "https://www.buffalobills.com/news/x"},
                  {"id": "2", "subject": "X", "kind": "other", "text": "salary note", "confidence": "high",
                   "source_url": "https://www.draftkings.com/lobby"}]})
    d = agents.parse_research([res])["BUF@MIA"]
    assert [f.id for f in d.facts] == ["BUF@MIA.F1"] and d.ruled_out == ["Some Guy"]


def test_strategist_cuts_need_verified_facts(toy_contest):
    contest, projs = toy_contest
    k = "T00:WR0"
    contest.players[k] = contest.players[k].model_copy(update={"name": "Alpha Receiver"})
    key = k
    facts = [Fact(id="G.F1", subject="Alpha Receiver", team="T00", kind="injury", text="Alpha Receiver ruled out (hamstring)",
                  confidence="high"),
             Fact(id="G.F2", subject="Somebody Else", team="T05", kind="matchup", text="tough corner", confidence="high")]
    d = {"T00@T01": _dossier(contest, "T00@T01", facts)}
    idx = agents.fact_index(d)
    out = {"remove_not_playing": [{"player_key": key, "fact_ids": ["G.F1"]}],
           "cuts": [{"player_key": "T00:QB0", "fact_ids": ["G.F2"], "reason": "feeling"},
                    {"player_key": "T00:RB0", "fact_ids": ["NOPE"], "reason": "made up"}],
           "close_calls": [], "weakest_pick": {"player_key": "T00:QB0", "why": "x"}, "preferred_draft": "cash"}
    excluded, rep = agents.apply_strategist(out, idx, contest, d)
    # toy keys are "T00:WR0" (no name part), so name matching runs on the display name we set
    assert "T00:QB0" not in excluded and "T00:RB0" not in excluded
    assert len(rep["rejected"]) >= 2


def test_checker_verdict_only_fails_on_backed_blockers(toy_contest):
    contest, projs = toy_contest
    pts = {k: p.mean for k, p in projs.items()}
    lu = solve(contest, pts)
    pick = lu.slots[1].player_key
    team = contest.players[pick].team
    name = contest.players[pick].name
    facts = [Fact(id="G.F1", subject=name, team=team, kind="status", text=f"{name} is out", confidence="high")]
    d = {"g": GameDossier(game="g", facts=facts)}
    idx = agents.fact_index(d)
    ok, fails, _ = agents.checker_verdict({"attacks": [
        {"player_key": pick, "claim": "not_playing", "severity": "blocker", "fact_ids": ["G.F1"], "argument": "out"},
        {"player_key": lu.slots[2].player_key, "claim": "bad_matchup", "severity": "blocker", "fact_ids": [], "argument": "vibes"},
    ]}, idx, contest, lu)
    assert not ok and fails == {pick}
    ok2, fails2, _ = agents.checker_verdict({"attacks": [
        {"player_key": pick, "claim": "not_playing", "severity": "blocker", "fact_ids": ["missing"], "argument": "?"}]},
        idx, contest, lu)
    assert ok2 and not fails2


def test_backup_adjustments_are_bounded_and_cited(toy_contest):
    contest, projs = toy_contest
    pts = {k: p.mean for k, p in projs.items()}
    k = next(iter(pts))
    p = contest.players[k]
    idx = {"F": Fact(id="F", subject=p.name, team=p.team, kind="role", text=f"{p.name} loses snaps", confidence="high")}
    new, removed, rep = agents.apply_backup({"adjustments": [
        {"player_key": k, "multiplier": 0.1, "fact_ids": ["F"], "reason": "role"},
        {"player_key": "T01:QB0", "multiplier": 1.3, "fact_ids": [], "reason": "hunch"}], "remove": []}, idx, contest, pts)
    assert new[k] == pytest.approx(pts[k] * 0.5) and new["T01:QB0"] == pts["T01:QB0"]


def test_forecast_parse_orders_and_validates():
    res = AgentResult(name="forecast:g", ok=True, output={"game": "g", "players": [
        {"player_key": "A", "low": 20, "likely": 10, "high": 5, "p_dnp": 1.4, "rationale": "r"},
        {"player_key": "ZZZ", "low": 1, "likely": 2, "high": 3, "p_dnp": 0, "rationale": "r"}]})
    f, problems = agents.parse_forecasts([res], {"A"})
    assert (f["A"].low, f["A"].likely, f["A"].high, f["A"].p_dnp) == (5, 10, 20, 1.0)
    assert problems and "ZZZ" in problems[0]


def test_blend_quantile_average(toy_contest):
    contest, projs = toy_contest
    k = "T00:WR0"
    b = {k: projs[k]}
    f = {k: Forecast(player_key=k, low=2, likely=10, high=25, p_dnp=0.1)}
    out = blend(b, f, contest.players, "nfl", weight_override=0.5)[k]
    assert out.p50 == pytest.approx(0.5 * 10 + 0.5 * projs[k].p50)
    assert out.mean == pytest.approx(mixture_mean(out.p10, out.p50, out.p90, out.p_zero), abs=1e-3)
    assert forecast_weight("nfl", "WR") == 0.85 and forecast_weight("nba", "PG") == 0.0
    learned = {"nfl": {"WR": {"n": 500, "weight": 0.4}}}
    assert forecast_weight("nfl", "WR", learned) == 0.4


def test_update_blend_shrinks(tmp_path):
    p = tmp_path / "blend.json"
    errs = [{"position": "WR", "baseline_err": 5.0, "forecast_err": 10.0}] * 100
    st = update_blend(p, "nfl", errs, prior_weight=0.85, n0=400)
    w = st["nfl"]["WR"]["weight"]
    assert 0.2 < w < 0.85  # moved toward the better baseline, not all the way
    assert json.loads(p.read_text())["nfl"]["WR"]["n"] == 100


def test_grade_against_hindsight(toy_contest):
    contest, projs = toy_contest
    pts = {k: p.mean for k, p in projs.items()}
    lu = solve(contest, pts)
    actual = {k: v * (0.5 if i % 3 else 1.6) for i, (k, v) in enumerate(pts.items())}
    g = grade_lineup(contest, lu, actual, projections=projs,
                     stage_lineups={"baseline_only": lu, "final": lu})
    assert g.hindsight_optimal >= g.actual and 0 < g.efficiency <= 1
    assert len(g.per_slot) == 9


def test_generic_provider_drops_out_players():
    from pathlib import Path
    from dfs.io.contest_file import load_contest
    c = load_contest(Path(__file__).parent / "fixtures" / "dk_salaries_classic.csv")
    ctx = GenericProvider().prepare(c)
    outs = {k for k, p in c.players.items() if (p.injury_status or "").upper() == "OUT"}
    assert outs and outs <= ctx.ruled_out and not (outs & set(ctx.baseline))
    q = next(k for k, p in c.players.items() if p.injury_status == "Q")
    assert ctx.baseline[q].p_zero == 0.25


def test_sources_gate_and_blind():
    require("nflverse")
    with pytest.raises(SourceNotAllowed):
        require("fantasypros")
    assert host_blocked("https://www.draftkings.com/x") and not host_blocked("https://www.nfl.com/news")
    assert blind_text("He is a full participant in practice") is not None
    for leak in ("Bills are -3.5 favorites", "salary of $7,800", "scored 24.5 fantasy points", "implied team total 27"):
        assert blind_text(leak) is None
