"""Agent-stage plumbing with a fake backend: parsing, citation checks, code verdicts, blending, grading."""

from __future__ import annotations

import asyncio
import json

import pytest

from dfs.blend import blend, forecast_weight
from dfs.distributions import mixture_mean
from dfs.grade import grade_lineup, update_blend
from dfs.llm.backend import AgentResult, AgentTask, FakeBackend, Runner
from dfs.io.contest_file import norm_name
from dfs.models import Fact, Forecast, GameDossier
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


def test_sdk_backend_options_and_tool_counting(monkeypatch):
    """The SDK is called with deny-by-default permissions, and searches are counted from the tool calls."""
    sdk = pytest.importorskip("claude_agent_sdk")
    from dfs.llm.backend import SDKBackend
    from dfs.sources import FETCH_ALLOW_DOMAINS

    seen = {}

    async def fake_query(prompt, options):
        seen["opts"] = options
        yield sdk.AssistantMessage(content=[sdk.ToolUseBlock(id="1", name="WebSearch", input={"query": "x"}),
                                            sdk.ToolUseBlock(id="2", name="WebSearch", input={"query": "y"})],
                                   model="m")
        yield sdk.ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=2,
                                session_id="s", total_cost_usd=0.01, usage={"output_tokens": 5},
                                structured_output={"answer": "ok"}, permission_denials=[{"tool_name": "WebFetch"}])

    monkeypatch.setattr(sdk, "query", fake_query)
    t = AgentTask(stage="research", name="r", system="s", prompt="p", tools=["WebSearch", "WebFetch"],
                  schema={"type": "object", "properties": {"answer": {"type": "string"}}})
    r = asyncio.run(SDKBackend().run(t))
    o = seen["opts"]
    assert r.ok and r.output == {"answer": "ok"} and r.web_searches == 2
    assert r.usage["permission_denials"] == 1
    assert o.permission_mode == "dontAsk" and o.setting_sources == []
    assert "WebFetch" not in o.allowed_tools  # never a blanket fetch grant
    assert sorted(a for a in o.allowed_tools if a.startswith("WebFetch(")) == sorted(
        f"WebFetch(domain:{d})" for d in FETCH_ALLOW_DOMAINS)
    assert set(o.tools) == {"WebSearch", "WebFetch"}  # no Bash/Read/Write/Edit available to agents


def test_standings_captain_rows_do_not_double_count(tmp_path):
    from dfs.grade import read_standings
    p = tmp_path / "contest-standings.csv"
    p.write_text("Rank,EntryId,EntryName,TimeRemaining,Points,Lineup,,Player,Roster Position,%Drafted,FPTS\n"
                 "1,1,a,0,150.5,x,,Josh Allen,FLEX,61.20%,24.4\n"
                 "2,2,b,0,140.0,x,,Josh Allen,CPT,22.80%,36.6\n"
                 "3,3,c,0,120.0,x,,Dalton Kincaid,CPT,5.00%,15\n")
    st = read_standings(p)
    assert st.actual_fpts["josh allen"] == 24.4  # FLEX row, not the 1.5x captain row
    assert st.ownership["josh allen"] == 61.2 and st.ownership["josh allen|CPT"] == 22.8
    assert st.actual_fpts["dalton kincaid"] == 10.0  # only a captain row: divided back


def test_user_rules_reject_unknown_keys():
    from pydantic import ValidationError
    from dfs.models import UserRules
    with pytest.raises(ValidationError):
        UserRules(lock=["X"], bring_back=1)  # a typo or an unsupported rule is an error, not ignored
    with pytest.raises(ValidationError):
        UserRules(max_exposure={"X": 0.3})


def test_update_blend_keeps_prior_without_evidence(tmp_path):
    st = update_blend(tmp_path / "b.json", "nfl", [{"position": "QB", "baseline_err": 1.0, "forecast_err": 1.0}] * 40,
                      prior_weight=0.85)
    assert st["nfl"]["QB"]["weight"] == 0.85


@pytest.mark.parametrize("text", [
    "Seahawks −2.5", "The over-under is 42.5", "a total of 42.5", "Game total sits at 51.5",
    "Sportsbooks have the Rams laying 12.5 points", "Oddsmakers project LA for 32 points", "LA team total of 32",
    "Seattle by 2.5 points", "Puka Nacua prop: over 7.5 receptions", "Nacua costs 9,800 on DraftKings", "priced at 9.8K",
    "AvgPointsPerGame: 24.6", "site average is 24.6", "scored 31.2 PPR last week", "a low-end WR1 this week",
    "the WR12 in rankings", "Josh Allen $8,200", "12% owned in GPPs", "Seattle by 2.5", "Total: 42.5",
    "Nacua posted 31.2 points in Week 5", "He has scored 25.3 points per game this year", "He put up 31.4 last week",
    "Buffalo's implied 27.5", "the line is BUF by 6.5", "BUF -6\u00bd", "a pick'em game", "Mahomes is listed at 7800",
    "Seattle laying a field goal at home", "Arizona is getting three points at home",
    "Bettors expect a shootout with 51.5 points"])
def test_blind_drops_leaks(text):
    assert blind_text(text) is None


@pytest.mark.parametrize("text", [
    "Lamar Jackson (ankle) listed questionable after missing two games.", "He is the team's WR1 with a 28% target share.",
    "Bills ranked 3rd in pass defense", "Skyy Moore (WR, GB): official report Out (Ankle); practice: Did Not Participate",
    "Total of 3 touchdowns this season", "He played 64% of snaps", "Kyle Pitts owned the red zone",
    "DK Metcalf (WR, PIT): official report Questionable (Hamstring)", "Drew Lock will start in Las Vegas",
    "He averaged 4.5 yards per carry over the last three games"])
def test_blind_keeps_facts(text):
    assert blind_text(text) == text


def test_blocked_hosts_and_betting_paths():
    assert host_blocked("https://www.prizepicks.com/playbook-article/x")
    assert host_blocked("https://www.si.com/nfl/betting/week-7-odds")
    assert not host_blocked("https://www.nfl.com/news/injury-report")


# --------------------------------------------------------------------------- review regressions (agents)

def _named_contest():
    from tests.conftest import make_toy_contest
    c, _ = make_toy_contest()
    names = {("T00", "RB0"): ("ATL", "Bijan Robinson", "RB"), ("T01", "RB0"): ("WAS", "Brian Robinson Jr.", "RB"),
             ("T02", "QB0"): ("BUF", "Josh Allen", "QB"), ("T02", "QB1"): ("BUF", "Kyle Allen", "QB"),
             ("T02", "WR0"): ("BUF", "Khalil Shakir", "WR"), ("T02", "TE0"): ("BUF", "Dalton Kincaid", "TE"),
             ("T02", "DST0"): ("BUF", "Bills", "DST"), ("T03", "WR0"): ("JAX", "Brian Thomas Jr.", "WR"),
             ("T04", "TE0"): ("LV", "Ian Thomas", "TE")}
    players = {}
    for k, p in c.players.items():
        team, pos = k.split(":")
        if (team, pos) in names:
            t, n, ps = names[(team, pos)]
            key = f"{t}|dst|DST" if ps == "DST" else f"{t}|{norm_name(n)}|{ps}"
            players[key] = p.model_copy(update={"key": key, "name": n, "team": t, "positions": [ps]})
    return c.model_copy(update={"players": players, "candidates": []})


def test_fact_about_needs_the_players_own_subject():
    c = _named_contest()
    F = lambda subj, team, text="x": Fact(id="F", subject=subj, team=team, kind="status", text=text)
    assert not agents.fact_about(F("Brian Robinson Jr.", "WAS", "ruled out (ankle)"), "ATL|bijan robinson|RB", c)
    assert not agents.fact_about(F("Kyle Allen", "BUF", "inactive"), "BUF|josh allen|QB", c)
    assert not agents.fact_about(F("Dalton Kincaid", "BUF", "ruled out; Shakir expected to see more targets"),
                                 "BUF|khalil shakir|WR", c)
    assert not agents.fact_about(F("Josh Allen", "BUF", "ruled out with a concussion"), "BUF|dst|DST", c)
    assert not agents.fact_about(F("Ian Thomas", "LV", "ruled out"), "JAX|brian thomas|WR", c)
    assert not agents.fact_about(F("Allen", "BUF", "out"), "BUF|josh allen|QB", c)  # two Allens on BUF
    assert agents.fact_about(F("Josh Allen (QB)", "BUF"), "BUF|josh allen|QB", c)
    assert agents.fact_about(F("Shakir", "BUF"), "BUF|khalil shakir|WR", c)  # unique last name + team
    assert agents.fact_about(F("Bills defense", "BUF"), "BUF|dst|DST", c)
    assert agents.fact_about(F("BUF", "BUF"), "BUF|dst|DST", c)


def test_says_out_ignores_negations_and_practice():
    F = lambda text, status=None: Fact(id="F", subject="X", kind="status", text=text, status=status)
    assert not agents.says_out(F("James Cook sat out Wednesday's practice but returned Friday and will play"))
    assert not agents.says_out(F("Travis Kelce (ankle) is not ruled out and is expected to play"))
    assert not agents.says_out(F("Doubtful with a hamstring"))
    assert agents.says_out(F("Ruled out for Sunday (concussion)"))
    assert agents.says_out(F("Skyy Moore (WR, GB): official report Out (Ankle); practice: DNP"))
    assert agents.says_out(F("Placed on injured reserve Tuesday"))
    assert agents.says_out(F("anything", status="inactive")) and not agents.says_out(F("ruled out", status="active"))


def test_team_level_facts_cannot_cut_a_named_teammate():
    c = _named_contest()
    shakir_role = Fact(id="R1", subject="Khalil Shakir", team="BUF", kind="role", text="named full-time slot starter")
    team_role = Fact(id="R2", subject="BUF", team="BUF", kind="role", text="new play-caller, more two-TE sets")
    idx = {f.id: f for f in (shakir_role, team_role)}
    assert agents._verified(["R1"], idx, "BUF|dalton kincaid|TE", c) == []
    assert [f.id for f in agents._verified(["R2"], idx, "BUF|dalton kincaid|TE", c)] == ["R2"]


def test_backup_removals_need_a_cited_out_fact():
    c = _named_contest()
    out = Fact(id="S1", subject="Josh Allen", team="BUF", kind="status", text="ruled out (concussion)", confidence="high")
    pts = {k: 10.0 for k in c.players}
    _, removed, rep = agents.apply_backup({"adjustments": [], "remove": [{"player_key": "BUF|khalil shakir|WR", "fact_ids": []},
                                                                          {"player_key": "BUF|josh allen|QB", "fact_ids": ["S1"]}]},
                                          {"S1": out}, c, pts)
    assert removed == {"BUF|josh allen|QB"} and rep["rejected"][0]["remove"] == "BUF|khalil shakir|WR"
    _, removed, _ = agents.apply_backup({"adjustments": [], "remove": ["BUF|khalil shakir|WR"]}, {}, c, pts)
    assert removed == set()  # the old bare-string form never removes anyone


def test_forecaster_sees_no_summary_urls_or_leaks():
    d = GameDossier(game="A@B", summary="B favoured by 3.5; total 47.5", facts=[
        Fact(id="A@B.F1", subject="X", team="A", kind="role", text="named starter", source_url="https://news.example/x"),
        Fact(id="A@B.F2", subject="Y", team="A", kind="role", text="Y costs $7,800", source_url="https://news.example/y")])
    t = agents.blinded_facts_text({"A@B": d})
    assert "favoured" not in t and "https://" not in t and "$7,800" not in t and "named starter" in t


def test_research_prompt_has_time_and_no_market():
    from datetime import datetime, timezone
    from tests.conftest import make_toy_contest
    c, _ = make_toy_contest()
    t = agents.research_tasks(c, {}, {g: "T01 favoured by 3" for g in c.games},
                              now=datetime(2026, 10, 25, 14, 37, tzinfo=timezone.utc))[0]
    assert "favoured" not in t.prompt and "As of: 2026-10-25 14:00 UTC" in t.prompt
    r = AgentResult(name=f"research:{c.games[0]}", ok=True, output={"summary": "s", "ruled_out": [], "doubtful_or_questionable": [],
        "facts": [{"subject": "X", "kind": "status", "text": "a", "as_of": "2026-10-26", "confidence": "high",
                   "source_url": "https://www.nfl.com/a"},
                  {"subject": "Y", "kind": "status", "text": "b", "as_of": "2026-10-24T18:00", "confidence": "high",
                   "source_url": "https://www.nfl.com/b"}]})
    d = agents.parse_research([r], now=datetime(2026, 10, 25, 14, 0, tzinfo=timezone.utc))
    assert [f.subject for f in d[c.games[0]].facts] == ["Y"]  # the fact dated after the run's time is dropped


def test_runner_survives_a_corrupt_cache_file(tmp_path):
    be = FakeBackend({"strategist": lambda t: {"ok": 1}})
    t = AgentTask(stage="strategist", name="s", system="x", prompt="p", schema={"type": "object"})
    r = Runner(be, cache_dir=tmp_path)
    (tmp_path / f"{t.cache_key()}.json").write_text('{"output": {"ok"')  # truncated write
    res = asyncio.run(r.run(t))
    assert res.ok and not res.cached and len(be.calls) == 1


def test_grading_the_same_run_twice_counts_once(tmp_path):
    errs = [{"position": "WR", "baseline_err": 3.0, "forecast_err": 1.0}] * 40
    a = update_blend(tmp_path / "b.json", "nfl", errs, prior_weight=0.85, run_id="run1")
    b = update_blend(tmp_path / "b.json", "nfl", errs, prior_weight=0.85, run_id="run1")
    assert a["nfl"]["WR"]["n"] == b["nfl"]["WR"]["n"] == 40


def test_research_ruled_out_needs_a_proving_fact():
    c = _named_contest()
    g = c.players["BUF|josh allen|QB"].game
    bare = GameDossier(game=g, summary="", facts=[], ruled_out=["Josh Allen"])
    assert agents.ruled_out_keys(c, {g: bare}) == set()  # a bare name (or injected text) removes nobody
    proved = bare.model_copy(update={"facts": [Fact(id="F1", subject="Josh Allen", team="BUF", kind="status",
                                                    text="ruled out (concussion)", confidence="high",
                                                    source_url="https://www.buffalobills.com/news/x")]})
    assert agents.ruled_out_keys(c, {g: proved}) == {"BUF|josh allen|QB"}


def test_news_from_blocked_sites_is_ignored():
    c = _named_contest()
    keys = ["BUF|josh allen|QB", "BUF|khalil shakir|WR"]
    r = AgentResult(name="news:x", ok=True, output={"updates": [
        {"player": "Josh Allen", "status": "out", "text": "ruled out", "source_url": "https://sportsbook.fanduel.com/n"},
        {"player": "Khalil Shakir", "status": "out", "text": "ruled out"}]})
    news = agents.parse_news([r], c, keys)
    assert "BUF|josh allen|QB" not in news  # FanDuel is not a source
    assert news["BUF|khalil shakir|WR"]["status"] == "questionable"  # an unsourced 'out' is not acted on


def test_runner_stops_at_the_deadline_and_splits_the_budget():
    import time as _t
    seen = []

    class Slow:
        async def run(self, task):
            seen.append(task.max_budget_usd)
            return AgentResult(name=task.name, output={"ok": 1}, ok=True, cost_usd=0.1)

    r = Runner(Slow(), budget_usd=1.0, concurrency=4)
    t = AgentTask(stage="checker", name="c", system="x", prompt="p", schema={"type": "object"})
    res = asyncio.run(r.run(t))
    assert res.ok and seen[0] <= 1.0 / 4 + 1e-9  # one slot's share of what is left
    r.deadline_at = _t.monotonic() + 5
    res = asyncio.run(r.run(AgentTask(stage="checker", name="d", system="x", prompt="q", schema={"type": "object"})))
    assert not res.ok and "deadline" in res.error


def test_forecast_parse_accepts_an_unambiguous_truncated_key():
    r = AgentResult(name="forecast:A@B", ok=True, output={"game": "A@B", "players": [
        {"player_key": "IND|dst", "low": 1, "likely": 5, "high": 12, "p_dnp": 0, "rationale": "x"},
        {"player_key": "IND|j", "low": 1, "likely": 5, "high": 12, "p_dnp": 0, "rationale": "x"}]})
    fc, problems = agents.parse_forecasts([r], {"IND|dst|DST", "IND|j smith|WR", "IND|j jones|RB"})
    assert set(fc) == {"IND|dst|DST"} and len(problems) == 1


def test_web_facts_need_a_status_to_count_as_out():
    web = Fact(id="G.F3", subject="X", kind="injury", text="ruled out vs. BAL last week; Week 7 status not yet known")
    assert not agents.says_out(web)
    assert agents.says_out(web.model_copy(update={"status": "out"}))
    official = Fact(id="G.S4", subject="X", kind="injury", text="X (WR, GB): official report Out (Ankle)")
    assert agents.says_out(official)


def test_inactive_lists_under_a_team_subject_count_for_ruled_out():
    c = _named_contest()
    g = c.players["BUF|khalil shakir|WR"].game
    d = GameDossier(game=g, summary="", ruled_out=["Khalil Shakir"], facts=[
        Fact(id=f"{g}.F2", subject="BUF", team="BUF", kind="status", status="inactive", confidence="high",
             text="Bills inactives: WR Khalil Shakir, OL X, DL Y", source_url="https://www.buffalobills.com/x")])
    assert agents.ruled_out_keys(c, {g: d}) == {"BUF|khalil shakir|WR"}


def test_novelty_rule_only_for_forecast_players():
    c = _named_contest()
    f = Fact(id="G.F1", subject="Khalil Shakir", team="BUF", kind="role", text="loses the slot job", confidence="high")
    out = {"remove_not_playing": [], "cuts": [{"player_key": "BUF|khalil shakir|WR", "fact_ids": ["G.F1"], "reason": "x"}]}
    ex, _ = agents.apply_strategist(out, {"G.F1": f}, c, {}, priced={"G.F1"}, forecasted={"BUF|josh allen|QB"})
    assert ex == {"BUF|khalil shakir|WR"}  # never forecast, so the fact is new to his numbers
    ex, _ = agents.apply_strategist(out, {"G.F1": f}, c, {}, priced={"G.F1"}, forecasted={"BUF|khalil shakir|WR"})
    assert ex == set()


def test_runner_caps_never_exceed_the_budget():
    spent = []

    class Greedy:
        async def run(self, task):  # spends every cent it is allowed
            spent.append(task.max_budget_usd)
            return AgentResult(name=task.name, output={"ok": 1}, ok=True, cost_usd=task.max_budget_usd)

    r = Runner(Greedy(), budget_usd=2.0, concurrency=4)
    tasks = [AgentTask(stage="research", name=f"r{i}", system="x", prompt=f"p{i}", schema={"type": "object"}) for i in range(8)]
    asyncio.run(r.run_all(tasks))
    assert r.spent_usd <= 2.0 + 1e-6


def test_uncited_research_facts_are_dropped():
    r = AgentResult(name="research:A@B", ok=True, output={"summary": "", "ruled_out": [], "doubtful_or_questionable": [],
        "facts": [{"subject": "X", "kind": "status", "status": "out", "text": "ruled out", "confidence": "high"},
                  {"subject": "Y", "kind": "status", "status": "out", "text": "ruled out", "confidence": "high",
                   "source_url": "https://www.nfl.com/x"}]})
    assert [f.subject for f in agents.parse_research([r])["A@B"].facts] == ["Y"]


@pytest.mark.parametrize("text", ["The offensive line is banged up: C Cam Jurgens (concussion) out",
                                  "He is getting two more carries a game since the trade",
                                  "Implied in the coach's comments: he starts Sunday"])
def test_blind_keeps_ordinary_football_text(text):
    assert blind_text(text) == text


def test_one_team_fact_proves_outs_only_as_an_inactives_list():
    c = _named_contest()
    g = c.players["BUF|khalil shakir|WR"].game
    report = Fact(id=f"{g}.F1", subject="BUF", team="BUF", kind="injury", status="out", confidence="high",
                  text="Injury report: Khalil Shakir questionable, Dalton Kincaid out", source_url="https://x.com/a")
    d = GameDossier(game=g, summary="", ruled_out=["Khalil Shakir"], facts=[report])
    assert agents.ruled_out_keys(c, {g: d}) == set()  # one status cannot speak for several players


def test_forecaster_does_not_see_unproven_ruled_out_names():
    d = GameDossier(game="A@B", summary="", ruled_out=["Josh Allen"], facts=[])
    assert "Josh Allen" not in agents.blinded_facts_text({"A@B": d})


@pytest.mark.parametrize("text", ["The Chiefs are laying 7 at home against Denver", "Buffalo is laying 3.5 on the road",
                                  "Seattle getting 7 at home", "LA implied for 27", "The line is now Buffalo minus 3"])
def test_blind_drops_bare_spreads_and_implied_totals(text):
    assert blind_text(text) is None


@pytest.mark.parametrize("text", ["He is getting two more carries a game since the trade",
                                  "The offensive line is down 2 starters after Jurgens' concussion",
                                  "The Jets defense is allowing 28 points per game since Sauce Gardner went on IR"])
def test_blind_keeps_football_facts_with_numbers(text):
    assert blind_text(text) == text


def test_team_fact_counts_only_for_the_players_own_clause():
    c = _named_contest()
    g = c.players["BUF|khalil shakir|WR"].game
    mixed = Fact(id=f"{g}.F1", subject="BUF", team="BUF", kind="injury", status="out", confidence="high",
                 source_url="https://x.com/a", text="Dalton Kincaid out; Khalil Shakir, inactive in Week 6, is questionable")
    single = mixed.model_copy(update={"id": f"{g}.F2", "text": "Bills: Khalil Shakir ruled out (ankle)"})
    for f, want in ((mixed, set()), (single, {"BUF|khalil shakir|WR"})):
        d = GameDossier(game=g, summary="", ruled_out=["Khalil Shakir"], facts=[f])
        assert agents.ruled_out_keys(c, {g: d}) == want


@pytest.mark.parametrize("url", ["https://www.rotoballer.com/player-news/coby-white-out/1234",
                                 "https://www.cbssports.com/fantasy/basketball/news/grant-williams-out/",
                                 "https://www.nbcsports.com/fantasy/basketball/player-news/x"])
def test_fantasy_news_sources_are_blocked(url):
    assert host_blocked(url)


def test_league_and_news_sources_stay_allowed():
    assert not host_blocked("https://www.cbssports.com/nba/news/hornets-injury-report/")
    assert not host_blocked("https://www.nba.com/news/injury-report")
