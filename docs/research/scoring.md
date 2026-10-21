# DraftKings and FanDuel scoring rules: fact-checked research for the DFS lineup engine

Research date: 2026-10-21. Topic key: `scoring`. This file replaces `scoring.md`. It keeps that file's design
material (tables, edge cases, worked examples, engine design) and corrects it where needed.

Inline tags:
- `[VERIFIED: url]`: I re-confirmed the fact today from the cited source. The source type is noted (see §0.2).
- `[CORRECTED: was X; url]`: the original was wrong or incomplete. The fixed fact follows.
- `[UNVERIFIED]`: I could not independently re-confirm it today. It is carried over from `scoring.md` with that file's
  source, and must be checked before real money is entered.

---

## 0. How this check was done, and its limits

### 0.1 Access in this session (observed today)

- `WebFetch` returned `getaddrinfo ENOTFOUND` for `www.draftkings.com`, `www.fanduel.com`, `dknetwork.draftkings.com`,
  `pick6.draftkings.com`, `www.rotowire.com` and `www.stokastic.com`. The session proxy's status endpoint lists
  `connect_rejected` (HTTP 403 on CONNECT) for `www.rotowire.com`, `www.fantasypros.com`, `www.nfl.com`, `www.espn.com` and
  others. [VERIFIED: tool errors and `$HTTPS_PROXY/__agentproxy/status`, this session]
- `WebSearch` refused: "this turn's web search budget is used up (limit: 200 WebSearch calls per turn, shared by every agent
  in it)". [VERIFIED: WebSearch tool response, this session]
- **Reachable:** `github.com`, `raw.githubusercontent.com`, GitHub release downloads and the GitHub code-search tool. All of
  today's checks therefore use GitHub-hosted material. That includes **captured site API responses and real site CSV exports**
  (primary data, but captured by third parties on known dates), **verbatim or dated transcriptions of the sites' rules
  pages** committed to repos, independent scoring implementations, and **nflverse play data** for re-computing the 2026
  worked examples.
- No DK or FD page was read live today. **Before go-live, every table must still be checked against the contest's own
  "Rules & Scoring" text** (§8).

### 0.2 Source index for this check (V-numbers are cited inline)

| ID | Source | Type and date |
|---|---|---|
| V1 | https://github.com/gacolitti/draft.kings/tree/HEAD/tests/testthat (`.../lineups/{1,70,81,96}/rules.json`, `.../159/rules.json`) | **Primary capture**: DK API `/lineups/v1/gametypes/{id}/rules` responses (date unknown) |
| V2 | https://raw.githubusercontent.com/kingoffrisco/FantasAI/HEAD/app/src/lib/dkContestDetails.js | Transcription of the DK NFL Classic rules page, "verified live 2026-08-22 against DK's own rules page" |
| V3 | https://raw.githubusercontent.com/bsamaha/nfl_data/HEAD/catalog/draftkings/classic.yml | DK NFL Classic table, `version: "2025-09"`, `source: "DraftKings Rules Page"` |
| V4 | https://raw.githubusercontent.com/frosty720/DDMLB/966bfeb05b8623deae2395b00bfd9d530c819e89/public/themes/draftdaily/app/views/contest/contest/scripts/new-lineup.phtml | A ~2018 DK-clone site that copies DK's NFL and NBA rules text verbatim, including the DST points-allowed note |
| V5 | https://raw.githubusercontent.com/nickp10/mcubed-lineup-insight-data/4ea3e55f782188c6834ac5d5a4cf607e6e689438/spec-content/fanDuelGeminiContest.json and `.../spec-content/fanDuelContest.json` | **Primary capture**: FD API fixture-list JSON with `scoring_rules`, `salary_cap` and `roster_positions` (NFL 2019-09-22; MLB 2019-08-19) |
| V6 | https://raw.githubusercontent.com/djsensei/lineup_optimizer/08be30f81ca1fd8695cb22d635f08c5f74b2559d/points_predictor.py ; https://raw.githubusercontent.com/marek5050/FantasyFootball/4b8f755f257f3043a0b8bdf3ae25255ddb9af644/data/cplusplus/FantasyFootballParser/src/PointCalculator.cpp | Verbatim FD NFL rules text, around 2014–2015 |
| V7 | https://raw.githubusercontent.com/Beexly/Sports/3a9c6e4bceb473837245b14ab89e125a71c887a9/docs/dfs/research/2026-09-13/verify/papers-scoring-strategy-2026-09-13.md | 2026-09-13 research note quoting fanduel.com/rules from search crawls "294 days ago and 72 days ago — identical both times" |
| V8 | https://raw.githubusercontent.com/drgregmscott-tech/DFS_Optimizer/ec92934f597448fa5340f6df56cd344ad84cd1fb/scripts/scoring_rules.py ; https://github.com/drgregmscott-tech/DFS_Optimizer/blob/HEAD/scripts/ingest_salaries.py | 2026 implementation. It transcribes the DK Showdown kicker rules (user-supplied, 2026-08-04), checks FD single-game cap arithmetic on a live roster builder (Aug 2026), and verifies DST scoring against 544 real graded 2021 DK team-weeks |
| V9 | https://github.com/drgregmscott-tech/DFS_Optimizer/tree/HEAD/data/raw_salaries (`dk_showdown_wk6_DAL_GB_18Oct2026.csv`, `dk_classic_wk6_main_18Oct2026.csv`, `fd_classic_wk6_main_18Oct2026.csv`, `26_27_Season_Salary_Archives/Week_1/fd_showdown_09Sep2026_ne_sea.csv`) ; https://github.com/jackdegen/nba-props/tree/HEAD/data (`current-fanduel-sg.csv`) | **Primary data**: real DK and FD salary exports, Sep–Oct 2026, plus a 2026 FD NBA single-game list |
| V10 | https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_2026.csv (Last-Modified 2026-10-21 09:05 GMT) ; https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv | nflverse weekly player stats and schedule/scores, 2026 Weeks 1–6 |
| V11 | https://raw.githubusercontent.com/jonrossclaytor/nba/3992a5e27aaba1f8b1bb926d27e9d244a7c77bf8/dk_scoring.txt (plus 6 other verbatim copies found by code search, e.g. al-chen/draftbetter `rules.txt`, and V4) | Verbatim DK NBA rules text, 2014–2018 |
| V12 | https://raw.githubusercontent.com/hamaadasif/nba-dfs-optimizer/a3570b0b853ae9f629dfd11442cfd1e92406e7fe/data_pipeline.py ; https://raw.githubusercontent.com/dschrengost/projections-v2/HEAD/projections/fpts_v2/scoring.py | 2026 NBA implementations for DK and FD. **They disagree on the DK bonuses** (see §1.4) |
| V13 | https://raw.githubusercontent.com/mmfoodchicks/kalshi-betting-helper/6fd56af53b1cf6508a472ae7abde81e0fdb0b922/dk_scoring.py | 2026 multi-sport DK table "audited against DraftKings' published tables" (MLB, NFL, K, DST, NASCAR, F1, UFC) |
| V14 | https://raw.githubusercontent.com/agavriel/nhl_python/7ae25a25c6a2258f04d97d7fac255aa4cf9fc1d8/dfs_cleaning_functions.py ; https://raw.githubusercontent.com/michaelhmiv/Sportfolio/0f517d8d998c4e5cb392c05a48eeba7802f32c96/server/nhl-fantasy-scoring.ts | DK NHL implementations (2025–26). They disagree on the 5+ SOG and 3+ BLK bonuses |
| V15 | https://github.com/iposton/angular-fantasy-sports/blob/HEAD/src/app/services/nhl-util.service.ts | FD NHL goalie formula (older) |
| V16 | https://raw.githubusercontent.com/pureslurp/DKPGA/679ee8f046ee9bd91b8c9e8ccfc66483c904fa39/backtest/pga_dk_scoring.py | DK PGA implementation that quotes the rules in docstrings |
| V17 | pydfs-lineup-optimizer 3.6.1 (PyPI sdist), `sites/*/settings.py` ; https://github.com/DimaKudosh/pydfs-lineup-optimizer | Open-source roster, cap and team-limit encodings (~2021–22) |
| V18 | https://github.com/BenBrostoff/draftfast/blob/master/draftfast/rules.py | Open-source rules. Quotes FD support: "Up to five players from same team, provided one is a pitcher." |
| V19 | https://github.com/dachhack/ffgame/blob/HEAD/packages/core/src/sports/mlb.ts | 2026 note restating the DK MLB points set |
| site_formats Wn | Search snippets that another agent gathered in `/tmp/claude-0/-home-user-daily-fantasy-sports/7b39a0e6-5812-5e7e-9a48-82f39febae8b/scratchpad/research/site_formats.md`, **not re-checked today**: W1 https://www.fanduel.com/research/fanduel-launches-new-single-game-daily-fantasy-format ; W4 https://linestar-nfl-dfs-player-props.beehiiv.com/p/top-nfl-dfs-plays-props-eagles-vs-cowboys-kickoff-edition-single-game-dfs-strategy-tips ; W5 https://www.fanduel.com/nhl-guide?t=rules ; W8 https://dfshero.com/help/sports/cfb ; W9 https://www.pff.com/news/fantasy-week-1-college-dfs-plays-on-draftkings ; W10 https://www.stokastic.com/articles/dfs-strategy/college-football-showdown-strategy ; W11 https://www.thefantasyfootballers.com/dfs/dfs-101-beginners-guide-fanduel | Used only as corroboration |

Confidence after this check:
- **high**: a primary capture (V1, V5, V9, V10) or a DK/FD-dated 2025–26 transcription (V2, V3, V7, V8), corroborated by a second independent source.
- **medium**: one dated transcription, or several older but agreeing verbatim copies.
- **low**: a single secondary implementation, or conflicting sources.

### 0.3 Status of the original's meta-claims

| Original claim | Verdict |
|---|---|
| WebFetch ENOTFOUND for DK, FD, RotoGrinders and Wikipedia; archive.org "unable to fetch"; curl to DK got CONNECT 403 | [VERIFIED: same errors reproduced this session for DK, FD, dknetwork, pick6, rotowire, stokastic; proxy status shows connect_rejected]. I did not retry archive.org (an archive service is off-limits as a search workaround). |
| The search cache of fanduel.com/rules is about 518 days old (~2025-05-21) | [UNVERIFIED]. This varies by search. V7 (2026-09-13) reports crawls 294 and 72 days old (about 2025-11-23 and about 2026-07-03) and says the two were identical. **A copy from around July 2026 exists, and it shows the 2024 yardage bonuses.** |
| WebSearch budget is 200 per turn, shared, and it ran out | [VERIFIED: WebSearch tool response, this session] |

---

## 1. Machine-readable scoring tables

Conventions (used directly by the engine in §6):
- `linear`: points = weight × stat. Yards are per yard.
- `thresholds`: a one-time bonus when `stat >= gte`. Inclusive `>=` is ASSUMED from the "300+" and "100+" labels.
  [UNVERIFIED: the exact-boundary case has not been seen in site data]
- `tiers`: exactly one tier applies, on integer `[min, max]`, where `max: null` means unbounded.
- `multi_cat`: a bonus when at least *k* listed categories are ≥ 10.
- Every table carries `source`, `verified` and `confidence`.

### 1.1 NFL — DraftKings (Classic and Showdown share the player and DST scoring)

```yaml
dk.nfl.classic:
  source:
    - https://raw.githubusercontent.com/kingoffrisco/FantasAI/HEAD/app/src/lib/dkContestDetails.js   # V2, DK rules page transcribed 2026-08-22
    - https://raw.githubusercontent.com/bsamaha/nfl_data/HEAD/catalog/draftkings/classic.yml          # V3, 2025-09
    - V4 (2018 verbatim DK text); V1 (DK API gametype 1); V10 (nflverse 2026 re-computation of the DK examples)
    - original: https://dknetwork.draftkings.com/2025/08/27/nfl-dfs-beginners-guide-draftkings/ (not fetchable today)
  confidence: high
  roster: [QB, RB, RB, WR, WR, WR, TE, FLEX(RB/WR/TE), DST]
  # [VERIFIED: V1 gametype 1 lineupTemplate = QB,RB,RB,WR,WR,WR,TE,FLEX,DST; "Create a 9-player lineup while staying under the $50,000 salary cap"]
  salary_cap: 50000            # [VERIFIED: V1 salaryCap.maxValue = 50000; V2; V3]
  lineup_rules:                # OMISSION in original, added:
    min_games: 2               # [VERIFIED: V1 gameCount.minValue = 2, error code MULTIPLE_GAMES]
    min_teams: 2               # [VERIFIED: V1 teamCount.minValue = 2, "must include players from at least two different teams"]
    late_swap: true            # [VERIFIED: V1 allowLateSwap = true]
  offense:
    linear:
      pass_yds: 0.04          # "25 Passing Yards = +1 Pt (+0.04/Yd)"         [VERIFIED: V2, V3, V4]
      pass_td: 4              #                                               [VERIFIED: V2, V3, V4]
      int_thrown: -1          # "Interception Thrown -1"                      [VERIFIED: V2, V3, V4]
      rush_yds: 0.1           #                                               [VERIFIED: V2, V3, V4; V10 E2]
      rush_td: 6              #                                               [VERIFIED: V2, V3, V4; V10 E2]
      rec: 1                  # full PPR                                      [VERIFIED: V2, V3, V4; V10 E1/E3]
      rec_yds: 0.1            #                                               [VERIFIED: V2, V3; V10 E1]
      rec_td: 6               #                                               [VERIFIED: V2, V3; V10 E3]
      return_td: 6            # "Punt/Kickoff/FG Return TD +6"                [VERIFIED: V2, V3 ("Punt/Kickoff/FG Return for TD")]
      fumble_lost: -1         #                                               [VERIFIED: V2, V3, V4]
      two_pt: 2               # "2 Pt Conversion (Pass, Run, or Catch)"       [VERIFIED: V3, V4; V10: Isaiah Likely Wk6 2026 = 23.9 needs his 2-pt catch]
      off_fumble_rec_td: 6    # "Offensive Fumble Recovery TD"                [VERIFIED: V2, V3, V4]
    thresholds:
      - {stat: pass_yds, gte: 300, pts: 3}   # "300+ Yard Passing Game +3"       [VERIFIED: V2, V3, V4] (original said medium)
      - {stat: rush_yds, gte: 100, pts: 3}   # "100+ Yard Rushing Game +3"       [VERIFIED: V2, V3; V10 E2 Walker 128 yds]
      - {stat: rec_yds,  gte: 100, pts: 3}   # "100+ Receiving Yard Game +3"     [VERIFIED: V2, V3; V10 E1 Nabers 165 yds]
  dst:
    linear:
      sack: 1                         # [VERIFIED: V2, V3, V4; V10 E4/E5]
      interception: 2                 # [VERIFIED: V2, V3, V4; V10 E4/E5]
      fumble_recovery: 2              # [VERIFIED: V2, V3, V4; V10 E4/E5]
      return_td: 6                    # "Punt/Kickoff/FG Return TD" (V2); V4 lists "Kickoff Return TD" and "Punt Return TD" separately  [VERIFIED: V2, V4]
      int_return_td: 6                # [VERIFIED: V2, V4]
      fumble_recovery_td: 6           # [VERIFIED: V2, V4; V10 E5 (NE LB fumble-return TD)]
      blocked_punt_or_fg_return_td: 6 # [VERIFIED: V2, V4]
      safety: 2                       # [VERIFIED: V2, V3, V4]
      blocked_kick: 2                 # [VERIFIED: V2, V3, V4]
      two_pt_or_xp_return: 2          # V2 "2-Pt/XP Conversion Return +2"; V3 two_point_return 2  [VERIFIED: V2, V3] (original said medium, secondary label)
    points_allowed_tiers:             # [VERIFIED: V2, V3, V4, V13; V10 reproduces E4 (PA 6 → +7) and E5 (PA 3 → +7)]
      - {min: 0,  max: 0,    pts: 10}
      - {min: 1,  max: 6,    pts: 7}
      - {min: 7,  max: 13,   pts: 4}
      - {min: 14, max: 20,   pts: 1}
      - {min: 21, max: 27,   pts: 0}
      - {min: 28, max: 34,   pts: -1}
      - {min: 35, max: null, pts: -4}
    yards_allowed_tiers: none         # no yards-allowed line in V2, V3 or V4  [VERIFIED: V2, V3, V4]
    points_allowed_definition: >      # [VERIFIED: V4 verbatim (2018); V8 empirical on 544 real 2021 DK team-weeks]
      V4 verbatim: "The following scoring plays will result in Points Allowed (PA) by your Defense/Special Teams (DST):
      Rushing TDs, Passing TDs, Punt Return TDs, Kick Return TDs and Blocked Punt TDs; 2pt conversions; Extra-points;
      Field-goals. *NOTE: Points Allowed (PA) only includes points surrendered while DST is on the field - doesn't include
      points given up by team's offense (i.e. points off offensive turnovers)."
      So pick-sixes, fumble-return TDs and safeties scored against this team's offense are excluded. Return TDs allowed
      (kick, punt, blocked punt) ARE included. V8 independently found that "opponent final score minus opponent DEFENSIVE TDs"
      best reproduces real graded DK DST points (89.7% exact match, mean bias -0.16).
dk.nfl.showdown:
  inherits: dk.nfl.classic (offense + dst)
  source: [V1 gametype 96 (rulesUrl /help/rules/1/96), V9 DK Showdown export DAL@GB 2026-10-18, V8]
  confidence: high
  roster: [CPT, FLEX, FLEX, FLEX, FLEX, FLEX]   # [VERIFIED: V1 gametype 96; "Create your team from 1 game"]
  salary_cap: 50000                             # [VERIFIED: V1 gametype 96 salaryCap.maxValue = 50000]
  lineup_rules: {min_teams: 2, late_swap: false} # OMISSION added. [VERIFIED: V1 gametype 96 teamCount.minValue = 2, allowLateSwap = false]
  slot_multipliers: {CPT: {points: 1.5, salary: 1.5}, FLEX: {points: 1.0, salary: 1.0}}
  # points 1.5x: [VERIFIED: V1 CPT positionTip "Scores 1.5x Fantasy Points"]
  # salary 1.5x: [VERIFIED: V9 dk_showdown_wk6_DAL_GB_18Oct2026.csv: CPT row Salary / FLEX row Salary = 1.5 for all 55 players]
  eligible_positions: [QB, RB, WR, TE, K, DST]  # [VERIFIED: V9 same file has K (3) and DST (2) players with both CPT and FLEX rows]
  kicker:                                       # [CORRECTED: was "LOW – recollection, UNCONFIRMED"; V8 (DK "NFL Showdown Captain Mode" rules transcribed 2026-08-04), V13]
    linear: {xp_made: 1}
    fg_made_by_distance:
      - {min: 0,  max: 39,   pts: 3}
      - {min: 40, max: 49,   pts: 4}
      - {min: 50, max: null, pts: 5}
    missed_fg_or_xp: 0                          # V8 quotes DK: "Kickers are only eligible for extra points and field goals made"; V13: "a miss costs nothing"
    other_stats: none                           # same quote: a kicker scores nothing else (e.g. a kicker's 2-pt run or tackle)  [VERIFIED: V8]
  confidence_kicker: medium                     # one transcription (V8) plus one implementation (V13); confirm on the live rules tab
dk.cfb.showdown:                                # OMISSION added (from site_formats W10, Stokastic Jul 2026 snippet)  [UNVERIFIED]
  roster: [CPT, FLEX x5], salary_cap: 50000, cpt: {points: 1.5, salary: 1.5}, min_one_player_per_team: true
```

### 1.2 NFL — FanDuel

```yaml
fd.nfl.classic:
  source:
    - V5 FD API scoring_rules (2019-09-22)  - V6 FD rules text (~2014–15)
    - V7 fanduel.com/rules quoted from a ~2026-07 crawl  - V9 FD classic export, Wk6 2026
    - original: https://www.fanduel.com/research/daily-fantasy-football-what-you-should-know-about-fanduel-s-updated-scoring-for-2024 (not fetchable today)
  confidence: high (per-stat values), medium (2024 bonuses)
  roster: [QB, RB, RB, WR, WR, WR, TE, FLEX(RB/WR/TE), DEF]
  # [VERIFIED: V9 fd_classic_wk6_main_18Oct2026.csv roster positions QB, RB/FLEX, WR/FLEX, TE/FLEX, DEF, with no K rows; V17]
  salary_cap: 60000           # [VERIFIED: V17 FanDuelSettings.budget = 60000; site_formats W11] (original: recollection)
  lineup_rules: {max_per_team: 4, min_teams: 3}   # OMISSION added. [VERIFIED: V17 max_from_one_team = 4, min_teams = 3; site_formats W11]
  kicker_in_classic: false    # removed for 2018, replaced by FLEX  [VERIFIED: V9 Wk6 2026 export has no K; V8 "FD has carried no K slot since 2018"]
  offense:
    linear:                   # FD API abbr/full/points in brackets  [VERIFIED: V5, V6, V7]
      pass_yds: 0.04          # [PaY "Passing Yards" 0.04]
      pass_td: 4              # [PaTD 4]; V7 quotes "Passing Touchdown = 4 Points"
      int_thrown: -1          # [I "Interceptions" -1]
      rush_yds: 0.1           # [RuY 0.1]
      rush_td: 6              # [RuTD 6]
      rec: 0.5                # [Re "Receptions" 0.5]; V7 quotes "Points per Reception = 0.5 Points"
      rec_yds: 0.1            # [ReY 0.1]
      rec_td: 6               # [ReTD 6]
      return_td: 6            # [KR/TD "Kickoff Return TDs" 6, PR/TD "Punt Return TDs" 6]
      fumble_lost: -2         # [FU/L "Fumbles Lost" -2]
      # [CORRECTED: was "official page wording is 'Fumble = -2 Points'"; FD's own API labels it "Fumbles Lost" (V5), and the 2014 text says "Fumbles lost = -2pts" (V6). The value -2 is unchanged.]
      two_pt_scored: 2        # [2PC/S "Two-point Conversions Scored" 2]
      two_pt_pass: 2          # [2PC/P "Two-point Conversion Passes" 2]
      off_fumble_rec_td: 6    # [FU/TD "Own Fumbles Recovered TDs" 6]
    thresholds:               # added for 2024
      - {stat: pass_yds, gte: 300, pts: 3}   # V7 quotes "300+ Passing Yard Bonus = 3 Points"     [VERIFIED: V7]
      - {stat: rush_yds, gte: 100, pts: 3}   # V7 quotes "100+ Rushing Yard Bonus = 3 Points"     [VERIFIED: V7]
      - {stat: rec_yds,  gte: 100, pts: 3}   # V7 quotes "100+ Receiving Yard Bonus = 3 Points"   [VERIFIED: V7]
    bonus_conflict_note: >
      V8 (a 2026 repo) encodes FD with NO bonuses, based on 2014–2021 graded data, which predates the 2024 change. That is
      stale. The FD page quoted in V7 (crawl ~2026-07) lists the bonuses. Default: bonuses ON; confirm with reconcile.py on the first FD slate.
  defense:
    linear:
      sack: 1                 # V6 "Sacks = 1pt"; V7 "sacks 1"                              [VERIFIED: V6, V7]
      interception: 2         # V6 "Interceptions made = 2pts"                              [VERIFIED: V6, V7]
      fumble_recovery: 2      # V6 "Opponent-fumbles recovered = 2pts"                      [VERIFIED: V6, V7]
      return_td: 6            # V6 "Return touchdowns = 6pts"; V7 "kick/punt return TD 6"   [VERIFIED: V6, V7]
      defensive_td: 6         # V6 "Fumble return touchdowns = 6pts"; V7 "defensive TD 6"   [VERIFIED: V6, V7]
      safety: 2               # [VERIFIED: V6, V7]
      blocked_kick: 2         # V6 (2014) "Blocked kicks = 2pts"; V7 (2026) "blocked punt 2"  [VERIFIED: V6, V7]. Whether FG/XP blocks also score under the 2026 label is [UNVERIFIED]
      xp_return: 2            # [UNVERIFIED]: not in V6 or V7. Original cites fanduel.com/rules and RotoGrinders snippets
    points_allowed_formula: "6*(rush_td + rec_td + own_fumble_recovered_for_td) + 2*two_pt_conversions + extra_points + 3*field_goals"
    # [VERIFIED: V5 notes verbatim "For purposes of defensive scoring, points allowed are calculated as: 6 * (Rushing TD +
    #  Receiving TD + Own fumbles recovered for TD ) + 2 * (Two point conversions) + Extra points + 3 *(Field Goals)"; V6 same]
    # V6 (2015) also adds: "Blocked punt return touchdowns are excluded from defensive scoring." Current status [UNVERIFIED].
    points_allowed_tiers:     # full-game slates  [VERIFIED: V6, V7]
      - {min: 0,  max: 0,    pts: 10}
      - {min: 1,  max: 6,    pts: 7}
      - {min: 7,  max: 13,   pts: 4}
      - {min: 14, max: 20,   pts: 1}
      - {min: 21, max: 27,   pts: 0}
      - {min: 28, max: 34,   pts: -1}
      - {min: 35, max: null, pts: -4}
    empirical_note: >
      V8 ran the DK table against real FD 2014–2021 graded DST points: 79.8% exact match, versus 89.7% for DK. Same sign,
      no systematic offset. The lower FD match rate fits the FD formula excluding return TDs, but this is not proven.
      Keep the two conventions separate and let reconcile.py decide.
    points_allowed_tiers_2nd_half_slate:     # [UNVERIFIED] (original: fanduel.com/rules snippet); higher rows unknown
      - {min: 0,  max: 0,  pts: 7}
      - {min: 1,  max: 6,  pts: 4}
      - {min: 7,  max: 13, pts: 1}
      - {min: 14, max: 20, pts: 0}
      - {min: 21, max: 27, pts: -1}
      - {min: 28, max: null, pts: UNCONFIRMED}
    points_allowed_tiers_4th_quarter_slate:  # [UNVERIFIED] (original: fanduel.com/rules snippet); higher rows unknown
      - {min: 0,  max: 0,  pts: 4}
      - {min: 1,  max: 6,  pts: 1}
      - {min: 7,  max: 13, pts: 0}
      - {min: 14, max: 20, pts: -1}
      - {min: 21, max: 27, pts: -4}
      - {min: 28, max: null, pts: UNCONFIRMED}
    yards_allowed_tiers: none                # none in V5, V6 or V7  [VERIFIED: V6, V7]
  kicker:                     # single-game only  [VERIFIED: V5 FGu20/FGu30/FGu40 "0-19/20-29/30-39 Yards" = 3, FGu50 "40-49" = 4, FGo50 "50+" = 5, XP "Extra-point Conversions" = 1; V6 same; V8 2026-08-04 FD screenshot]
    linear: {xp_made: 1}      # (V8 could not see XP in its screenshot; V5 and V6 show XP = 1)
    fg_made_by_distance:
      - {min: 0,  max: 39,   pts: 3}
      - {min: 40, max: 49,   pts: 4}
      - {min: 50, max: null, pts: 5}
    missed_fg_or_xp: 0        # no miss line in V5 or V6  [VERIFIED: V5, V6]
fd.nfl.single_game:
  inherits: fd.nfl.classic (offense + defense + kicker)
  roster: [MVP, FLEX, FLEX, FLEX, FLEX, FLEX]       # 6 slots since 2025
  # [VERIFIED: V8 "1 MVP + 5 FLEX = 6 total (confirmed against a live ... contest)", Sep 2026; site_formats W1–W4]
  slot_multipliers: {MVP: {points: 1.5, salary: 1.5}, FLEX: {points: 1.0, salary: 1.0}}
  # [VERIFIED: V9 fd_showdown_09Sep2026_ne_sea.csv and SEA@TEN export: column "MVP 1.5x Salary" = 1.5 × "Salary" on every row; Roster Position "MVP - 1.5X Points/AnyFLEX"]
  # The MVP multiplier also applies to kicker points (screenshot MVP column 7.5/6/4.5 = 1.5 × FLEX)  [VERIFIED: V8]
  salary_cap: 60000
  # [CORRECTED: was "low/medium – Stokastic"; V8 checked the cap against a live FD roster builder (Aug 2026): "MVP $12,000 + 5 FLEX x $8,000 = $52,000 of the $60,000 cap, leaving exactly $8,000"]
  eligible_positions: [QB, RB, WR, TE, K, D]        # [VERIFIED: V9 FD single-game exports include K and D rows] (the original did not list D)
  csv_shape_note: "FD single-game export = ONE row per player with a 'MVP 1.5x Salary' column; DK Showdown = TWO rows per player (CPT and FLEX)"  # [VERIFIED: V9, V8]
  legacy_pre_2025: {roster: [MVP, FLEX, FLEX, FLEX, FLEX], mvp_points: 1.5, mvp_salary: 1.0}   # history only [UNVERIFIED]
```

### 1.3 College football (CFB)

```yaml
dk.cfb.classic:
  source: [original: pick6.draftkings.com CFB page, RotoWire, ETR (not fetchable today); V17]
  scoring: same as dk.nfl.classic.offense     # [UNVERIFIED] (original: medium; no independent copy found today)
  differences_from_nfl:
    - no DST and no kickers                    # [UNVERIFIED] (consistent with the V17 roster, which has no DST or K)
    - tight ends are listed as WR              # [UNVERIFIED]
    - roster: [QB, RB, RB, WR, WR, WR, FLEX(RB/WR), SUPERFLEX(QB/RB/WR)], salary cap 50000
      # [VERIFIED: V17 DraftKingsCollegeFootballSettings = QB,RB,RB,WR,WR,WR,FLEX(RB,WR),SUPER FLEX(QB,RB,WR), budget 50000, min_games 2; site_formats W9]
      # Read the slots from the entries file. RotoWire also described a 9-player variant [UNVERIFIED]
    - overtime: "full additional stats in the first two overtime periods"; from the 3rd OT on, only successful two-point tries score  # [UNVERIFIED] (ETR)
fd.cfb.classic:
  source: [original: fanduel.com/rules college section (snippet); FD 2023 helper article]
  scoring: half PPR; 0.04 per passing yard; 4 per passing TD; the rest assumed equal to fd.nfl.classic.offense   # [UNVERIFIED]
  yardage_bonuses: UNCONFIRMED (the 2024 NFL bonuses may or may not apply to CFB)                        # [UNVERIFIED]
  roster: [QB, RB, RB, WR, WR, WR, SUPERFLEX(QB/RB/WR)]      # 7 players; TEs count as WR   [UNVERIFIED] (site_formats W8 snippet)
  salary_cap: 60000                                         # [UNVERIFIED] (site_formats W8 snippet)
  overtime: "only the first two overtime periods count toward lineups"                     # [UNVERIFIED]
  availability: not offered in some states                                                 # [UNVERIFIED]
```

### 1.4 NBA (and WNBA)

```yaml
dk.nba.classic:
  source: [V11 verbatim DK text (2014–2018, 7 copies), V4, V12 (2026), V1 gametype 70; original: dknetwork 2025-10-17 NBA DFS 101]
  roster: [PG, SG, SF, PF, C, G, F, UTIL]   # [VERIFIED: V1 gametype 70 lineupTemplate; "Create an 8-player lineup while staying under the $50,000 salary cap. Includes Late Swap."]
  salary_cap: 50000                         # [VERIFIED: V1 gametype 70]
  lineup_rules: {min_games: 2, min_teams: 2}   # OMISSION added. [VERIFIED: V1 gametype 70 gameCount/teamCount minValue 2]
  linear: {pts: 1, fg3m: 0.5, reb: 1.25, ast: 1.5, stl: 2, blk: 2, tov: -0.5}
  # V11 verbatim: "Point = +1 PT; Made 3pt. shot = +0.5 PTs; Rebound = +1.25 PTs; Assist = +1.5 PTs; Steal = +2 PTs; Block = +2 PTs; Turnover = -0.5 PTs"
  # [VERIFIED: V11, V4, V12-hamaadasif (2026)]. V12-dschrengost (2026) OMITS the 3PM +0.5, so treat that file as wrong. Confirm with reconcile.py.
  multi_cat:
    categories: [pts, reb, ast, blk, stl]           # "MAX 1 PER PLAYER: Points, Rebounds, Assists, Blocks, Steals"  [VERIFIED: V11]
    bonuses:
      - {min_categories_ge_10: 2, pts: 1.5}         # "Double-Double = +1.5PTs"   [VERIFIED: V11]
      - {min_categories_ge_10: 3, pts: 3}           # "Triple-Double = +3PTs"     [VERIFIED: V11]
    stacking: cumulative   # a triple-double ALSO earns the double-double → +4.5 total
    # [UNVERIFIED: the sources conflict. V12-hamaadasif (2026) stacks (+4.5) and RotoWire (original) says it stacks;
    #  V12-dschrengost (2026) does NOT stack (+3 only). Default cumulative; reconcile.py must settle it on the first triple-double.]
dk.nba.showdown:
  roster: [CPT, UTIL, UTIL, UTIL, UTIL, UTIL]   # [VERIFIED: V1 gametype 81 (rulesUrl /help/rules/4/81), 1 game, $50,000 cap, teamCount min 2] (original: low)
  slot_multipliers: {CPT: {points: 1.5, salary: 1.5}}
  # points: [VERIFIED: V1 gametype 81 CPT "Scores 1.5x Fantasy Points"]; salary 1.5x for NBA: [UNVERIFIED] (verified only for NFL, V9). Read it from the file's CPT rows.
fd.nba.classic:
  source: [original: fanduel.com/rules snippet; V12-dschrengost (2026); xulianrenzoku/nba_dfs (2019) via code search; V17]
  roster: [PG, PG, SG, SG, SF, SF, PF, PF, C]       # 9 players  [VERIFIED: V17 FanDuelBasketballSettings]
  salary_cap: 60000                                 # [VERIFIED: V17; FD's own player-picker JS "FD.playerpicker.salaryCap = 60000" in github.com/funa68/stats fanduel_salarylist_Dec03.html]
  linear: {pts: 1, reb: 1.2, ast: 1.5, blk: 3, stl: 3, tov: -1}   # no 3PM, DD or TD bonuses   [VERIFIED: V12-dschrengost; xulianrenzoku "Steal = 3pts", "Turnover = -1pt"]
  # equivalently 3-pt FG 3, 2-pt FG 2, FT 1
  note: '"In Single Stat formats, not all of the above stats will count towards scoring" (official snippet)'   # [UNVERIFIED]
fd.nba.single_game:
  current: "1 MVP (1.5x points, 1.5x salary) + UTIL slots"
  # [CORRECTED: was "current_claim (medium, FD announcement)" with legacy "MVP 2x, STAR 1.5x, PRO 1.2x" shown as a live alternative;
  #  a 2026 FD NBA single-game export (V9 jackdegen/nba-props current-fanduel-sg.csv) shows Roster Position "MVP - 1.5X Points/UTIL" and an
  #  "MVP 1.5x Salary" column equal to 1.5 × Salary on all 34 rows. The STAR/PRO format is obsolete.]
  slot_count: 6 (1 MVP + 5 UTIL)   # [UNVERIFIED]: FD's 2025 announcement says the 6-slot format is "available for all sports"; the CSV does not show slot count
  rule_for_engine: read slot multipliers and count from the contest file and rules, never hard-code them
dk.wnba.classic:
  scoring: same as dk.nba.classic                    # [UNVERIFIED]
  roster: [G, G, F, F, F, UTIL], salary cap 50000    # [VERIFIED: V17 DraftKingsWNBASettings (G=PG/SG, F=SF/PF, UTIL), DraftKingsSettings.budget 50000] (old encoding)
fd.wnba:
  roster: [G, G, G, F, F, F, F], salary cap 40000    # OMISSION added. [VERIFIED: V17 FanDuelWnbaSettings] (old encoding, ~2021)
  scoring: believed same as fd.nba                   # [UNVERIFIED]
```

### 1.5 MLB

```yaml
dk.mlb.classic:
  source: [V13 (2026), V19 (2026), V17, V18; original: dknetwork 2020 MLB scoring, Pick6, RotoBaller, RotoWire]
  roster: [P, P, C, 1B, 2B, 3B, SS, OF, OF, OF]   # salary cap 50000   [VERIFIED: V17 DraftKingsBaseballSettings (P = SP/RP), budget 50000] (original: low)
  lineup_rules: {min_games: 2, max_hitters_per_team: 5}   # OMISSION added. [VERIFIED: V17 min_games 2; V18 position_per_team_rules non-P ≤ 5, "Can start two pitchers and 5 hitters"]
  hitter_linear: {single: 3, double: 5, triple: 8, hr: 10, rbi: 2, run: 2, bb: 2, hbp: 2, sb: 5}   # [VERIFIED: V13, V19]
  hitter_caught_stealing: 0   # [UNVERIFIED]: V13 and V19 (2026) have no CS line, while RotoWire lists -2. Default 0 and flag for reconciliation
  pitcher_linear: {ip: 2.25, k: 2, win: 4, er: -2, h_allowed: -0.6, bb_allowed: -0.6, hbp_allowed: -0.6}   # IP 2.25 = 0.75 per out   [VERIFIED: V13 "out": 0.75, V19 "0.75 = 2.25/IP"]
  pitcher_bonus: {complete_game: 2.5, cg_shutout: 2.5, no_hitter: 5}   # [VERIFIED: V13, V19]. Stacking (a CGSO earns CG + CGSO = +5) [UNVERIFIED]
  no_loss_penalty: true       # no loss line in V13 or V19   [VERIFIED: V13, V19]
  cross_role: hitting stats of pitchers and pitching stats of hitters do not count   # [UNVERIFIED] (Pick6 wording)
fd.mlb.classic:
  source: [V5 FD API "MLB Full Roster" fixture list 2019-08-19; V18; original: fanduel.com/rules]
  roster: [P, C/1B, 2B, 3B, SS, OF, OF, OF, UTIL]   # [VERIFIED: V5 roster_positions; UTIL = C/1B/2B/3B/SS/OF] (original: low)
  salary_cap: 35000                                 # [VERIFIED: V5 salary_cap 35000; V17] (original: low)
  hitter_linear: {single: 3, double: 6, triple: 9, hr: 12, rbi: 3.5, run: 3.2, bb: 3, sb: 6, hbp: 3}   # [VERIFIED: V5]
  pitcher_linear: {win: 6, quality_start: 4, er: -3, k: 3, ip: 3}   # [VERIFIED: V5] V5 note: "IP: Fractional scoring per out." (1 point per out)
  quality_start_definition: "When a starting pitcher completes at least six innings and permits no more than three earned runs."  # [VERIFIED: V5 verbatim]
  no_hit_or_walk_penalty: true        # V5 pitcher group has no H or BB line   [VERIFIED: V5]
  rules:
    - "max 5 players from one team, provided one is the pitcher (i.e. max 4 hitters)"   # [VERIFIED: V18 quoting support.fanduel.com "Up to five players from same team, provided one is a pitcher."]
    - "min 3 teams"                                     # [VERIFIED: V18 min_teams 3; V17]
    - "only one game of a doubleheader counts"           # [UNVERIFIED]
    - "suspended game: points count if >= 4.5 innings completed"   # [UNVERIFIED]
```

### 1.6 NHL

```yaml
dk.nhl.classic:
  source: [V14-agavriel (2025), V14-Sportfolio (2026), V17; original: Pick6 NHL page, dknetwork 2025-09-30 NHL DFS 101]
  roster: [C, C, W, W, W, D, D, G, UTIL]   # salary cap 50000   [VERIFIED: V17 DraftKingsHockeySettings (W = LW/RW; UTIL = C/W/D; min_teams 3, goalie excluded from the team count)]
  skater_linear: {goal: 8.5, assist: 5, sog: 1.5, blk: 1.3, sh_point_bonus: 2, shootout_goal: 1.5}
  # [VERIFIED: V14-agavriel (G 8.5, A 5, SOG 1.5, BLK 1.3, SH point 2 each); V14-Sportfolio adds shootout goal 1.5]
  skater_thresholds:
    - {stat: goals, gte: 3, pts: 3}          # hat trick   [VERIFIED: V14 both]
    - {stat: sog, gte: 5, pts: 3}            # [VERIFIED: V14-agavriel; V14-Sportfolio omits it. Conflict; default ON per agavriel and the original's DK sources]
    - {stat: blk, gte: 3, pts: 3}            # [VERIFIED: V14-agavriel; same conflict]
    - {stat: points, gte: 3, pts: 3}         # [VERIFIED: V14 both]
  goalie_linear: {win: 6, save: 0.7, goal_against: -3.5, shutout_bonus: 4, ot_loss: 2}   # [VERIFIED: V14-Sportfolio; V14-agavriel (save 0.7, GA -3.5, win 6)]
  goalie_thresholds: [{stat: saves, gte: 35, pts: 3}]                                    # [VERIFIED: V14-Sportfolio]
  shutout_rule: "must complete the entire game with 0 GA in regulation + OT; shootout goals do not prevent a shutout"   # [UNVERIFIED] (Pick6 wording). Note from V14-Sportfolio: a 0-GA relief appearance is not a shutout
  caveat: 'Pick6 page: "goals scored in shootout periods do not count towards player stat totals", against a +1.5 shootout-goal line. For Classic, score shootout goals only through shootout_goal, never as goals or SOG.'   # [UNVERIFIED]
fd.nhl.classic:
  source: [original: fanduel.com/rules snippet; V15; V17; site_formats W5]
  roster: [C, C, W, W, D, D, UTIL, UTIL, G]   # salary cap 55000   [VERIFIED: V17 FanDuelHockeySettings budget 55000; site_formats W5 FD NHL guide snippet] (original: low)
  skater_linear: {goal: 12, assist: 8, sog: 1.6, blk: 1.6, pp_point_bonus: 0.5, sh_point_bonus: 2}   # [UNVERIFIED] (no independent copy found today)
  goalie_linear: {win: 12, goal_against: -4, save: 0.8, shutout: 8}   # [VERIFIED: V15 formula saves*0.8 + shutouts*8 + wins*12 - GA*4] (older source)
  shootout: "no points for goals or saves during shootouts"            # [UNVERIFIED]
```

### 1.7 PGA golf

```yaml
dk.pga.classic:
  source: [V16; V17; original: dknetwork 2024-03-07 golf guide, RotoGrinders, RotoWire]
  roster: 6 x G, salary cap 50000   # [VERIFIED: V17 DraftKingsGolfSettings 6 G, budget 50000]
  per_hole: {double_eagle_or_better: 13, eagle: 8, birdie: 3, par: 0.5, bogey: -0.5, double_bogey_or_worse: -1}   # [VERIFIED: V16 docstring]
  bonuses:
    streak_3_birdies_or_better: 3        # [VERIFIED: V16]. Original says "max 1 per round"; V16's code awards +3 per separate 3-birdie streak. Conflict: [UNVERIFIED]
    bogey_free_round: 3                  # [VERIFIED: V16]
    all_4_rounds_under_70: 5             # "All 4 Rounds Under 70 Strokes = 5"   [VERIFIED: V16]
    hole_in_one: 5                       # [UNVERIFIED]: V16 uses 5 ("5 points per hole in one"), as did the 2018 source; older sources say 10
  finish_position:                       # [VERIFIED: V16 docstring and code, identical to the original]
    1: 30
    2: 20
    3: 18
    4: 16
    5: 14
    6: 12
    7: 10
    8: 9
    9: 8
    10: 7
    11-15: 6
    16-20: 5
    21-25: 4
    26-30: 3
    31-40: 2
    41-50: 1
  ties: UNCONFIRMED   # V16 maps "T5" to 5 and pays full 5th-place points, but that is the author's convention, not a quoted rule   [UNVERIFIED]
  variants: weekend, single-round and showdown contests use different values   # [UNVERIFIED]
fd.pga.classic:     # [UNVERIFIED] (original: RotoGrinders intro, TheGolfNewsNet 2017; low)
  roster: 6 x G     # [VERIFIED: V17 FanDuelGolfSettings 6 G, budget 60000 inherited] (cap [UNVERIFIED])
  per_hole: {eagle_or_better: 7, birdie: 3.1, par: 0.5, bogey: -1, double_bogey_or_worse: -3}
  finish_position: {1: 20, 2-5: 12, 6-10: 8, 11-25: 5, others: UNCONFIRMED}
  bonuses: {streak_2plus_under_par: 0.6, bounce_back: 0.3, round_5plus_birdies_or_better: 4, bogey_free_round: 5}
```

### 1.8 MMA (UFC) — DraftKings

```yaml
dk.mma.classic:
  source: [original: https://www.rotowire.com/mma/article.php?id=54707 (~2021); V13 (2026); V17]
  roster: 6 fighters, salary cap 50000   # [VERIFIED: V17 DraftKingsMMA 6 F, budget 50000] (old encoding)
  linear: {significant_strike: 0.2, control_time_sec: 0.03, takedown: 5, reversal_sweep: 5, knockdown: 10}   # [VERIFIED: V13]
  total_strikes_line: CONFLICT
  # [UNVERIFIED: the original (RotoWire ~2021) has "strikes +0.2 AND significant strikes +0.2" (a significant strike = 0.4).
  #  V13 (2026, "audited against DraftKings' published tables") shows only "Significant Strikes +0.2". The difference can be
  #  20+ points per fight. Check the live DK MMA scoring tab before using either.]
  win_bonus_by_round: {1: 90, 2: 70, 3: 45, 4: 40, 5: 40, decision: 30}   # [VERIFIED: V13]
  quick_win_bonus: 25      # "Quick Win Bonus (R1 ≤60s)"   [VERIFIED: V13]
  draw_or_no_contest: UNCONFIRMED   # [UNVERIFIED]
```

### 1.9 NASCAR and F1 — DraftKings (FanDuel NASCAR included)

```yaml
dk.nascar.classic:
  source: [V13 (2026); original: Stokastic, RotoWire 2023]
  roster: 6 drivers, salary cap 50000   # [VERIFIED: V17 DraftKingsNascarSettings 6 D, budget 50000]
  linear: {place_differential: 1.0, fastest_lap: 0.45, lap_led: 0.25}   # [VERIFIED: V13]
  place_differential: "starting position - finishing position (positive = gained)"   # [UNVERIFIED wording]
  finish_position: {1: 45, 2: 42, 3: 41, then: "-1 per place, extra -1 at 11th, 21st and 31st", 10: 34, 11: 32, 20: 23, 21: 21, 30: 12, 31: 10, 40: 1}
  # [VERIFIED: V13 full table 1:45, 2:42, 3:41, 4:40 … 10:34, 11:32 … 20:23, 21:21 … 30:12, 31:10 … 40:1; beyond 40th = 0]
  note: fastest laps are not awarded under caution   # [UNVERIFIED]
fd.nascar.classic:   # [UNVERIFIED] (original: fanduel.com/nascar?t=rules snippet; Fantasy Alarm 2019)
  roster: 5 drivers, salary cap 50000   # OMISSION added. [VERIFIED: V17 FanDuelNascarSettings 5 D, budget 50000] (old encoding)
  linear: {lap_completed: 0.1, lap_led: 0.1, place_differential: 0.5}
  finish_position: {1: 43, 2: 40, 3: 38, then: "-1 per place to 40th = 1"}
dk.f1.2026_changes:   # (original: https://dknetwork.draftkings.com/2026/03/05/scoring-changes-for-f1-dfs-that-draftkings-players-need-to-know/)  [UNVERIFIED as quoted]
  - finishing points now also paid outside the top 10 (22-car grid)
  - place differential: 1 point per position gained or lost (was bracketed)
  - lap led: raised from +0.1 to +0.25
  full_table_secondary:   # ADDED from V13 (2026), which agrees with all three changes above. One secondary source: [UNVERIFIED]
    finish: {1: 40, 2: 37, 3: 35, 4: 32, 5: 30, 6: 27, 7: 25, 8: 23, 9: 22, 10: 20, 11: 17, 12: 15, 13: 13, 14: 12, 15: 10, 16: 7, 17: 5, 18: 4, 19: 3, 20: 2, 21: 1, 22: 0}
    linear: {place_differential: 1, fastest_lap: 3, lap_led: 0.25, defeated_teammate: 5, classified: 1}
```

### 1.10 Soccer — DraftKings (partial)

```yaml
dk.soccer.classic:   # values [UNVERIFIED]: no independent copy found today (original: RotoWire DFS Soccer 101 articles)
  roster: [GK, D, D, M, M, F, F, UTIL(D/M/F)], salary cap 50000   # [VERIFIED: V17 DraftKingsSoccerSettings, min_teams 3] (original: low)
  linear: {goal: 10, assist: 6, shot: 1, shot_on_target: 1, shot_assist_or_chance_created: 1, cross: UNCONFIRMED(0.7 vs 0.75),
           tackle_won: 1, interception: 0.5, yellow_card: -1.5, red_card: -3}
  defender_clean_sheet: 3
  goalkeeper: {save: 2, clean_sheet: 5, penalty_save_extra: 3, goal_conceded: UNCONFIRMED(-2 recollection), win: UNCONFIRMED(5 recollection)}
  shootout: {goal: 1.5, miss: -1, gk_save: 1.5}
  other: fouls_drawn / fouls_conceded / passes / blocked shots / missed penalty – UNCONFIRMED
```

### 1.11 Tennis — DraftKings (still not researched)

```yaml
dk.tennis.classic:   # ALL VALUES [UNVERIFIED] (recollection; two GitHub code searches found nothing) – do not ship without verification
  roster: 6 players, salary cap 50000   # roster [VERIFIED: V17 DraftKingsTennisSettings 6 P, budget 50000]
  best_of_3: {match_played: 30, advanced: 6, set_won: 6, set_lost: -3, game_won: 2.5, game_lost: -2, ace: 0.4,
              double_fault: -1, break: 0.75, clean_set_bonus: 4, straight_sets_bonus: 6, no_double_fault_bonus: 2.5, aces_10plus_bonus: 2}
  best_of_5: {match_played: 30, advanced: 5, set_won: 5, set_lost: -2, game_won: 2, game_lost: -1.6, ace: 0.25,
              double_fault: -1, break: 0.5, clean_set_bonus: 2.5, straight_sets_bonus: 5, no_double_fault_bonus: 5, aces_15plus_bonus: 2}
```

---

## 2. Key differences between DK and FD that change player value

| Item | DraftKings | FanDuel | Impact |
|---|---|---|---|
| NFL reception | 1.0 [VERIFIED: V2] | 0.5 [VERIFIED: V5, V7] | DK raises high-target WR, TE and pass-catching RB |
| NFL fumble lost | -1 [VERIFIED: V2] | -2 [VERIFIED: V5] | FD penalizes fumble-prone RBs and QBs more |
| NFL yardage bonuses | +3 at 300 pass, 100 rush, 100 rec [VERIFIED: V2] | the same +3 values since 2024 [VERIFIED: V7] | both are nonlinear and need distributional projections |
| NFL DST points allowed | points allowed while the DST is on the field, including return TDs allowed [VERIFIED: V4, V8] | formula counting only offensive TDs, own-fumble-recovery TDs, 2-pt, XP and FG [VERIFIED: V5] | the same game can land in different PA tiers |
| NFL kicker | Showdown only: 3/4/5 by distance, XP 1 [VERIFIED: V8] | Single-game only: 3/4/5 by distance, XP 1 [VERIFIED: V5] | |
| Multiplier slot | CPT 1.5x points, 1.5x salary [VERIFIED: V1, V9] | MVP 1.5x points, 1.5x salary, 6 slots since 2025 [VERIFIED: V8, V9] | |
| CSV shape for the multiplier slot | two rows per player (CPT and FLEX) [VERIFIED: V9] | one row with a "MVP 1.5x Salary" column [VERIFIED: V9] | the parser must normalise |
| NBA stl / blk | 2 / 2 [VERIFIED: V11] | 3 / 3 [VERIFIED: V12] | FD raises defensive bigs and guards |
| NBA turnovers | -0.5 [VERIFIED: V11] | -1 [VERIFIED: V12] | |
| NBA bonuses | 3PM +0.5, DD +1.5, TD +3 [VERIFIED: V11]; stacking to 4.5 [UNVERIFIED] | none [VERIFIED: V12] | |
| MLB pitching | IP 2.25, K 2, W 4, ER -2, H/BB/HBP -0.6 [VERIFIED: V13] | IP 3, K 3, W 6, QS 4, ER -3 [VERIFIED: V5] | FD has no penalty for hits or walks allowed |
| NHL goal / assist | 8.5 / 5 [VERIFIED: V14] | 12 / 8 [UNVERIFIED] | FD weights goals more heavily against SOG and blocks |
| Team limits (NFL Classic) | ≥2 games, ≥2 teams [VERIFIED: V1] | ≤4 per team, ≥3 teams [VERIFIED: V17] | solver constraints |

---

## 3. Edge cases the scoring engine must handle

1. **DST points allowed is not the opponent's final score.**
   - FD: `6*(rush TD + rec TD + own-fumble-recovered TD) + 2*(2-pt conversions) + XP + 3*FG` [VERIFIED: V5]. It therefore
     EXCLUDES pick-sixes, fumble-return TDs, kick and punt return TDs, and safeties.
   - DK: the PA-counting plays are "Rushing TDs, Passing TDs, Punt Return TDs, Kick Return TDs and Blocked Punt TDs, 2pt
     conversions, Extra-points, Field-goals", and PA excludes "points given up by team's offense" [VERIFIED: V4]. Safeties are
     not on that list, so they are excluded.
   - Example: the opponent scores 27, made up of 2 offensive TD + 2 XP, 2 FG, and 1 kickoff-return TD + XP. DK PA = 27 → 0 pts.
     FD PA = 12 + 6 + 2 XP = 20 if only XPs after counted TDs count, or 21 if every XP counts (the formula says "Extra points")
     → +1 or 0 pts. **Which XPs count is [UNVERIFIED].** Test both against site-reported DST FPTS. (Arithmetic re-checked.)
   - The engine needs **scoring-play-level data** (play type and the unit on the field), not just box totals.
   - **nflverse pitfall (new, observed):** in the 2026 weekly stats, NE Week 2 LB Elijah Ponder shows `fumble_recovery_opp = 1`
     and `fumble_recovery_tds = 1` but `def_tds = 0` [VERIFIED: V10]. A DST aggregator that relies on `def_tds` alone misses
     fumble-return TDs. Sum `def_tds` with defenders' `fumble_recovery_tds`, or better, use play-by-play.
2. **Return TDs are credited twice, in different tables.** The returner gets +6 as an individual (DK "Punt/Kickoff/FG Return TD"
   [VERIFIED: V2]; FD "Kickoff/Punt Return TDs" [VERIFIED: V5]), and the returner's DST gets +6 too [VERIFIED: V2, V6]. Return
   yards score nothing on either site [VERIFIED: no return-yard line in V2, V5 or V6]. In Showdown, both can be rostered.
3. **Defensive scores stack.** INT returned for a TD = 2 + 6 = 8; blocked punt returned for a TD = 2 + 6 = 8; fumble-recovery
   TD = FR 2 + TD 6 = 8. [VERIFIED: V10 E5, where the NE DST's 21 = 4 sacks + INT 2 + FR 2 + PA(3) 7 + FR-TD 6 and the TD
   came on the same play as the recovery]. A safety (+2) is not a TD.
4. **Offensive fumble-recovery TD.** An offensive player who recovers a teammate's fumble in the end zone gets +6 as
   `off_fumble_rec_td` (DK "Offensive Fumble Recovery TD" [VERIFIED: V2]; FD "Own Fumbles Recovered TDs" [VERIFIED: V5]).
   The fumbler is charged a fumble lost only if the other team recovers [UNVERIFIED, standard stat rule].
5. **Two-point conversions.** On DK ("Pass, Run, or Catch") the passer and the scorer each get +2 [VERIFIED: V3, V4; V10
   Likely]. FD scores "Two-point Conversions Scored" 2 and "Two-point Conversion Passes" 2 [VERIFIED: V5]. Yards on 2-pt tries
   are not official yards [UNVERIFIED]. A defensive 2-pt or XP return gives the DK DST +2 [VERIFIED: V2]; on FD it is [UNVERIFIED].
6. **Bonus thresholds use separate categories.** Rushing and receiving yards are not combined [VERIFIED: separate lines in V2 and V7].
   Net yards are used (ASSUMED). Thresholds are inclusive at exactly 100 or 300 (ASSUMED from "100+"). A QB who rushes for
   100 also gets the rushing bonus (ASSUMED).
7. **Overtime.** NFL OT stats count (ASSUMED). CFB OT handling: see §1.3 [UNVERIFIED]. This needs period-level stats.
8. **FD 2nd-half and 4th-quarter-only slates.** Only stats from those periods count, and DEF PA uses the special tiers [UNVERIFIED].
9. **Slot multipliers.** CPT and MVP multiply the player's TOTAL points, including bonuses, DST tiers and kicker points, by 1.5
   [VERIFIED for FD kickers: V8 screenshot MVP column = 1.5 × FLEX column]. Round only after multiplying.
10. **Rounding.** All weights are exact decimals. Compute with `Decimal` and round only for display. DK NFL totals naturally carry
    two decimals because of 0.04 per passing yard (e.g. 299 yds = 11.96). CPT 1.5x can create a third decimal (31.55 × 1.5 =
    47.325) [VERIFIED: arithmetic]. The site rounding mode is [UNVERIFIED]. Accept ±0.01 when reconciling.
11. **MLB innings.** Box-score "6.2 IP" means 6⅔ innings = 20 outs. Convert to outs first. DK: 0.75 per out [VERIFIED: V13]. FD:
    "Fractional scoring per out", i.e. 1 per out [VERIFIED: V5]. FD QS = ≥ 6 IP and ≤ 3 ER [VERIFIED: V5]. CG + CGSO stacking on
    DK [UNVERIFIED]. Doubleheader and suspended-game rules [UNVERIFIED]. FD MLB C/1B is one combined slot [VERIFIED: V5].
12. **NBA double- and triple-doubles.** The categories are PTS, REB, AST, BLK, STL [VERIFIED: V11]. Max one of each bonus
    [VERIFIED: V11 "MAX 1 PER PLAYER"]. Whether a TD also earns the DD (+4.5) is [UNVERIFIED] because the sources conflict (V12).
13. **NHL.** The SH-point bonus is +2 per SH point, added on top of goal and assist points [VERIFIED: V14-agavriel]. A hat trick
    also triggers the 3+ points bonus [VERIFIED: V14 both, separate bonuses]. Shootout handling, the shutout definition and
    pulled-goalie treatment are [UNVERIFIED]. OTL +2 [VERIFIED: V14-Sportfolio].
14. **Golf.** Finish-point ties, playoff holes and the hole-in-one value are [UNVERIFIED]. Golfers who miss the cut get no finish
    points (V16 maps CUT → 100 → 0 points), but their holes played still score.
15. **NASCAR.** Place differential uses the official starting position. Cars sent to the rear or to a backup car are
    [UNVERIFIED]. No fastest-lap credit under caution [UNVERIFIED].
16. **Stat corrections.** Sites re-score after official stat corrections. The grading step must re-pull final stats. The time
    window is [UNVERIFIED].
17. **Pitchers hitting and hitters pitching**: ignore cross-role stats [UNVERIFIED for Classic].
18. **(New) Never treat nflverse `fantasy_points` or `fantasy_points_ppr` as DK or FD points.** nflverse scores INT at -2 and fumble
    lost at -2 and has no yardage bonuses (V8 measured this by reconstruction on 2021 data). Example from 2026 Week 6 [VERIFIED: V10]:
    Kenneth Walker III `fantasy_points_ppr` = 27.9 but DK = 30.9; Malik Nabers 28.5 vs DK 31.5; Chuba Hubbard 19.1 vs DK 22.1.
    The "game logs" baseline step must re-score raw stat lines with this engine.

---

## 4. Worked examples (these become unit tests)

Every arithmetic value below was re-run today with `Decimal`
(`/tmp/claude-0/-home-user-daily-fantasy-sports/7b39a0e6-5812-5e7e-9a48-82f39febae8b/scratchpad/vs_scripts/recheck_examples.py`),
and all of them match. **The stat lines of E1–E5 were independently re-derived today from nflverse 2026 data (V10)**, and the DK
table reproduces the DK totals the original took from DK's articles. The articles themselves could not be fetched today, so
the "(DK: x)" values are [UNVERIFIED] as quotations but agree with the verified table.

| # | Site | Player / unit | Stat line (nflverse check) | Computation | Expected |
|---|---|---|---|---|---|
| E1 | DK NFL | Malik Nabers, NYG vs NO, Wk 6 2026 | 12 rec, 165 yds, 0 TD [VERIFIED: V10] | 12 + 16.5 + 3 | **31.5** |
| E2 | DK NFL | Kenneth Walker III, KC vs LAC, Wk 6 2026 | 128 rush yds, 2 TD; 2-11 rec [VERIFIED: V10] | 12.8 + 12 + 3 + 2 + 1.1 | **30.9** |
| E3 | DK NFL | Germie Bernard, PIT vs TB, Wk 6 2026 | 11 rec, 87 yds, 1 TD [VERIFIED: V10] | 11 + 8.7 + 6 | **25.7** |
| E4 | DK DST | Chiefs DST, Wk 6 2026 (LAC 6 @ KC 22) | 4 sacks, 1 INT, 1 FR, PA 6 [VERIFIED: V10] | 4 + 2 + 2 + 7 | **15** |
| E5 | DK DST | Patriots DST, Wk 2 2026 (PIT 3 @ NE 20) | 4 sacks, 1 INT, 1 FR, PA 3, 1 **fumble-recovery** TD [VERIFIED: V10] | 4 + 2 + 2 + 7 + 6 | **21** |
| E6 | FD NFL | Nabers line (synthetic FD) | as E1 | 6 + 16.5 + 3 | **25.5** |
| E7 | FD NFL | Walker line (synthetic FD) | as E2 | 12.8 + 12 + 3 + 1 + 1.1 | **29.9** |
| E8 | DK & FD | QB, threshold inclusive (synthetic) | 300 pass yds, 2 TD, 1 INT, 20 rush yds | 12 + 8 + 3 − 1 + 2 | **24.0** (both) |
| E9 | DK & FD | QB, one yard short (synthetic) | 299 pass yds, rest as E8 | 11.96 + 8 − 1 + 2 | **20.96** (both) |
| E10 | DK / FD | RB with fumble (synthetic) | 99 rush yds, 1 TD, 3-20 rec, 1 fumble lost | DK 9.9+6+3+2−1 ; FD 9.9+6+1.5+2−2 | **DK 19.9 / FD 17.4** |
| E11 | DK SD | CPT Nabers (synthetic) | E1 in CPT | 31.5 × 1.5 | **47.25** |
| E12 | FD SG | MVP Walker (synthetic) | E7 in MVP | 29.9 × 1.5 | **44.85** |
| E13 | DK / FD NBA | Triple-double (synthetic) | 30 pts, 4 3PM, 10 reb, 10 ast, 2 stl, 1 blk, 3 TO | DK 30+2+12.5+15+4+2−1.5+1.5+3 ; FD 30+12+15+6+3−3 | **DK 68.5 (67.0 if no stacking) / FD 63.0** |
| E14 | DK / FD NBA | Double-double (synthetic) | 22 pts, 0 3PM, 12 reb, 3 ast, 1 stl, 2 blk, 2 TO | DK 22+15+4.5+2+4−1+1.5 ; FD 22+14.4+4.5+3+6−2 | **DK 48.0 / FD 47.9** |
| E15 | DK / FD MLB | Hitter (synthetic) | 1B, HR, 2 RBI, 2 R, 1 BB, 1 SB | DK 3+10+4+4+2+5 ; FD 3+12+7+6.4+3+6 | **DK 28 / FD 37.4** |
| E16 | DK / FD MLB | SP (synthetic) | 6.2 IP (20 outs), 8 K, W, 2 ER, 5 H, 1 BB | DK 15+16+4−4−3−0.6 ; FD 20+24+6−6+4 (QS) | **DK 27.4 / FD 48** |
| E17 | DK / FD MLB | CG shutout (synthetic) | 9 IP, 10 K, W, 0 ER, 3 H, 1 BB | DK 20.25+20+4−2.4+2.5+2.5 ; FD 27+30+6+4 | **DK 46.85 / FD 67** |
| E18 | DK / FD NHL | Skater (synthetic) | 1 G, 1 A, 5 SOG, 2 BLK, 1 PPP | DK 8.5+5+7.5+2.6+3 (5+ SOG) ; FD 12+8+8+3.2+0.5 | **DK 26.6 / FD 31.7** |
| E19 | DK / FD NHL | Hat trick (synthetic) | 3 G, 0 A, 6 SOG, 0 BLK, 1 PPP | DK 25.5+9+3+3+3 ; FD 36+9.6+0.5 | **DK 43.5 / FD 46.1** |
| E20 | DK / FD NHL | Goalie W (synthetic) | W, 36 SV, 2 GA | DK 6+25.2−7+3 ; FD 12+28.8−8 | **DK 27.2 / FD 32.8** |
| E21 | DK / FD NHL | Goalie SO (synthetic) | W, 30 SV, 0 GA, SO | DK 6+21+4 ; FD 12+24+8 | **DK 31.0 / FD 44.0** |
| E22 | DK PGA | Round (synthetic) | 5 birdies (3 in a row), 12 pars, 1 bogey | 15+6−0.5+3 (streak) | **23.5** |
| E23 | DK / FD DST | PA convention (synthetic) | opp 27: 2 off. TD + 2 XP, 2 FG, 1 KR TD + XP; DST 2 sacks | DK PA 27 → 0 ; FD PA 20 or 21 → +1 or 0 | **DK 2 / FD 3 or 2 (UNVERIFIED)** |
| E24 | DK NFL | **New.** Isaiah Likely, NYG vs NO, Wk 6 2026 | 6 rec, 99 yds, 1 TD, **1 receiving 2-pt conversion** [VERIFIED: V10] | 6 + 9.9 + 6 + 2 | **23.9** |
| E25 | DK NFL | **New.** Michael Penix Jr., ATL vs CHI, Wk 6 2026 | 225 pass yds, 3 TD, 0 INT, 1 rush yd, 1 fumble lost, 2 passing 2-pt [VERIFIED: V10] | 9 + 12 + 0.1 − 1 + 4 | **24.1** (DK article value [UNVERIFIED]) |
| E26 | DK NFL | **New.** Chuba Hubbard, CAR vs PHI, Wk 6 2026 | 120 rush yds, 1 TD; 1-1 rec [VERIFIED: V10] | 12 + 6 + 3 + 1 + 0.1 | **22.1** (DK article value [UNVERIFIED]) |

[CORRECTED: was "DK Isaiah Likely, reported '6-99-1 = 23.9', computes to 21.9, so the article omitted about 2 points
(likely a 2-pt catch)"; V10 shows Likely had exactly one receiving 2-pt conversion in Wk 6 2026, so DK's 23.9 is right and
confirms the +2 for a 2-pt catch (E24).] The original's general rule still holds: build tests from complete box scores, not from
article stat lines. E24–E26 are now complete and usable. Whether the article's Hubbard and Penix lines were incomplete is
[UNVERIFIED], since the article could not be fetched.

[CORRECTED: E5 was encoded in the unit tests as `int_return_td: 1`; the Patriots' DEF TD was a fumble-recovery TD (LB Elijah
Ponder, `fumble_recovery_opp = 1`, `fumble_recovery_tds = 1`), V10. The total is unchanged because both pay 6.]

---

## 5. Why scoring structure matters for projections

- Bonuses (+3 at 100 or 300 yards, NHL 5+ SOG, 3+ BLK, 35+ saves, NBA DD/TD, MLB QS, DST tiers) are **step functions**, so
  `score(mean stat line) ≠ mean(score(stat line))`.
  - Example: a WR with a mean of 95 yards and SD of about 30 has P(≥100) = 1 − Φ(0.167) ≈ 43%, so E[bonus] ≈ +1.3. Plugging in the
    mean gives 0. [VERIFIED: arithmetic, normal approximation]
  - The forecaster's "low / likely / high" must become a **stat distribution** (fit per-stat distributions to the three quantiles).
    The engine is applied **per simulation draw**, giving P10/P50/P90 fantasy points and P(bonus).
  - V8 measured on 2021 data that DK true scores differ from bonus-free PPR on 13.1% of all skill performances and on 42.9% of
    performances worth 15+ points (mean gap +1.22, max +7). Bonuses matter most in exactly the tournament-winning tail.
    [VERIFIED: V8, as that author reports]
- DST scoring depends on opponent points allowed, which is a game-level quantity. Simulate the game (team totals from betting lines)
  and derive PA, sacks and turnovers from it. Do not project DST points as an independent number.
- Correlations created by scoring (QB→WR on passing TDs and 2-pt plays, and RB versus the opposing DST) matter for lineup
  construction. Expose per-event credit ("a passing TD credits the QB +4 and the receiver +6") so the simulator can share draws.

---

## 6. Proposed scoring-engine design

**Layout**

```
dfs/scoring/
  tables/
    dk_nfl_classic.yaml  dk_nfl_showdown.yaml  fd_nfl_classic.yaml  fd_nfl_single_game.yaml
    dk_cfb_classic.yaml  fd_cfb_classic.yaml   dk_nba_classic.yaml  dk_nba_showdown.yaml  fd_nba_classic.yaml
    dk_mlb_classic.yaml  fd_mlb_classic.yaml   dk_nhl_classic.yaml  fd_nhl_classic.yaml
    dk_pga_classic.yaml  fd_pga_classic.yaml   dk_mma_classic.yaml  dk_nascar_classic.yaml ...
  engine.py        # pure functions, no I/O except table loading
  derive.py        # derived stats: singles, outs, QS, points(goals+assists), PA by site convention, DD/TD counts
  reconcile.py     # compare computed FPTS vs site-reported FPTS after a slate
  fd_rules_import.py   # NEW: converts an FD fixture-list `scoring_rules` JSON (abbr/full/points groups, see V5) into a table
tests/scoring/test_examples.py   # §4 table as parametrized pytest
```

**Table schema** (each YAML file):

```yaml
id: dk.nfl.classic
version: 2026-10-21            # bump on any change
verified: {status: partial, at: 2026-10-21, by: fact-check-agent, method: github-transcriptions+api-captures+nflverse-recompute}
sources: [ ... urls ... ]
roles:                         # a player is scored under one role
  offense: {linear: {...}, thresholds: [...]}
  dst:     {linear: {...}, tiers: {points_allowed: [...]}, derive: {points_allowed: dk_on_field}}
  kicker:  {linear: {...}, bucketed: {fg_made_yards: [...]}, only_stats: [xp_made, fg_made]}   # DK: "only eligible for extra points and field goals made"
role_by_position: {QB: offense, RB: offense, WR: offense, TE: offense, DST: dst, D: dst, K: kicker}
slots: {CPT: {points_mult: 1.5, salary_mult: 1.5}, FLEX: {points_mult: 1, salary_mult: 1}}
lineup_rules: {salary_cap: 50000, min_games: 2, min_teams: 2, max_per_team: null}   # NEW block: feeds the solver
period_filter: null            # e.g. {periods: [3,4,OT]} for FD 2nd-half slates, {max_ot: 2} for FD CFB
```

**Machine-readable rules where the site provides them (new finding).**
- FD's API fixture-list JSON carries `salary_cap`, `roster_positions` (`abbr`, `valid_player_positions`) and `scoring_rules`
  (`groups[].rules[] = {abbr, full, points}` plus free-text `notes`, for example the PA formula and the QS definition)
  [VERIFIED: V5]. If FD's terms allow reading it, `fd_rules_import.py` can build the table exactly and diff it against the YAML.
  The access and ToS question belongs to the data-sources topic.
- DK's `/lineups/v1/gametypes/{id}/rules` JSON carries `salaryCap`, `lineupTemplate` (slot names), `gameCount`/`teamCount` minimums,
  `allowLateSwap` and the CPT `positionTip` "Scores 1.5x Fantasy Points", but **no scoring table** [VERIFIED: V1]. V2's author
  writes that DK's scoring is "only ... text baked into an HTML contest-rules page". DK scoring therefore stays in YAML,
  checked by `reconcile.py`.

**Core API** (Python, `Decimal` throughout):

```python
@dataclass(frozen=True)
class LineItem: rule: str; stat_value: Decimal; points: Decimal

@dataclass(frozen=True)
class ScoreBreakdown:
    total: Decimal            # unrounded, after slot multiplier
    display: Decimal          # quantized to 0.01, ROUND_HALF_UP (site mode UNVERIFIED)
    items: tuple[LineItem, ...]

def load_table(table_id: str) -> ScoringTable: ...
def score(stats: Mapping[str, int | Decimal], table: ScoringTable, role: str,
          slot: str | None = None) -> ScoreBreakdown:
    """1) derive stats (derive.py), 2) linear, 3) thresholds, 4) tiers/buckets,
       5) multi-category bonuses, 6) sum, 7) apply slot multiplier, 8) quantize for display."""
def score_events(events: Iterable[PlayEvent], table, slate_filter) -> dict[player_id, ScoreBreakdown]:
    """Play-level path: needed for DST points allowed, period-filtered slates and simulation."""
def expected_score(samples: Iterable[Mapping[str, Decimal]], table, role, slot=None) -> Distribution:
    """Apply score() per simulated stat line → mean, P10/P50/P90, P(each bonus)."""
```

**Rules**
- Unknown stat keys raise an error. Stats a table doesn't use are ignored but logged once.
- Values tagged `[UNVERIFIED]` here are marked `unverified: true` in YAML. A table with any of them loads only with
  `allow_unverified=True`, and the orchestrator surfaces the warning in the run log and in the final hand-off file.
- `derive.points_allowed(convention="dk_on_field" | "fd_formula", scoring_plays)` keeps the site difference in one place.
- **Known conflicts get a switch, not a guess:** `dk.nba.classic.multi_cat.stacking`, `dk.mma.total_strikes_line`,
  `dk.nhl.skater_thresholds` (SOG and BLK), `dk.mlb.hitter_caught_stealing`, `dk.pga.streak_max_per_round`,
  `fd.nfl.defense.xp_return`, and `fd.dst.pa_xp_convention`. `reconcile.py` flips each switch to whichever setting reproduces
  site FPTS, then records the result in the YAML.
- `reconcile.py` takes the site's post-contest export of player FPTS and flags any |Δ| > 0.01. A mismatch means a rule is wrong. It
  feeds the grading step ("name the step that failed"): a scoring-table error is its own failure category, separate from
  forecast error.

**Unit tests** (pytest, parametrized from §4):

```python
@pytest.mark.parametrize("table,role,slot,stats,expected", [
  ("dk.nfl.classic","offense",None,{"rec":12,"rec_yds":165},"31.50"),                                   # E1
  ("dk.nfl.classic","offense",None,{"rush_yds":128,"rush_td":2,"rec":2,"rec_yds":11},"30.90"),         # E2
  ("dk.nfl.classic","offense",None,{"rec":11,"rec_yds":87,"rec_td":1},"25.70"),                        # E3
  ("dk.nfl.classic","dst",None,{"sack":4,"interception":1,"fumble_recovery":1,"points_allowed":6},"15.00"),  # E4
  ("dk.nfl.classic","dst",None,{"sack":4,"interception":1,"fumble_recovery":1,"points_allowed":3,"fumble_recovery_td":1},"21.00"),  # E5 (CORRECTED key)
  ("fd.nfl.classic","offense",None,{"rec":12,"rec_yds":165},"25.50"),                                   # E6
  ("dk.nfl.classic","offense",None,{"pass_yds":300,"pass_td":2,"int_thrown":1,"rush_yds":20},"24.00"),  # E8
  ("dk.nfl.classic","offense",None,{"pass_yds":299,"pass_td":2,"int_thrown":1,"rush_yds":20},"20.96"),  # E9
  ("fd.nfl.classic","offense",None,{"rush_yds":99,"rush_td":1,"rec":3,"rec_yds":20,"fumble_lost":1},"17.40"),  # E10
  ("dk.nfl.showdown","offense","CPT",{"rec":12,"rec_yds":165},"47.25"),                                 # E11
  ("fd.nfl.single_game","offense","MVP",{"rush_yds":128,"rush_td":2,"rec":2,"rec_yds":11},"44.85"),     # E12
  ("dk.nba.classic","player",None,{"pts":30,"fg3m":4,"reb":10,"ast":10,"stl":2,"blk":1,"tov":3},"68.50"),  # E13 (stacking=cumulative)
  ("fd.nba.classic","player",None,{"pts":30,"reb":10,"ast":10,"stl":2,"blk":1,"tov":3},"63.00"),       # E13
  ("dk.mlb.classic","pitcher",None,{"outs":20,"k":8,"win":1,"er":2,"h_allowed":5,"bb_allowed":1},"27.40"),  # E16
  ("fd.mlb.classic","pitcher",None,{"outs":20,"k":8,"win":1,"er":2},"48.00"),                          # E16 (QS derived)
  ("dk.nhl.classic","skater",None,{"goal":3,"assist":0,"sog":6,"blk":0},"43.50"),                       # E19
  ("dk.nhl.classic","goalie",None,{"win":1,"save":36,"goal_against":2},"27.20"),                        # E20
  ("dk.nfl.classic","offense",None,{"rec":6,"rec_yds":99,"rec_td":1,"two_pt":1},"23.90"),               # E24 (new, real)
  ("dk.nfl.classic","offense",None,{"pass_yds":225,"pass_td":3,"rush_yds":1,"fumble_lost":1,"two_pt":2},"24.10"),  # E25 (new, real)
  ("dk.nfl.showdown","kicker","CPT",{"xp_made":2,"fg_made_yards":[38,45,52]},"21.00"),                 # new synthetic: (2+3+4+5)=14 × 1.5
])
def test_examples(table, role, slot, stats, expected): ...
```

Additional property tests:
- Monotonicity: adding a positive stat never lowers the score, except for negative-weight stats.
- Threshold boundaries at `gte-1` and `gte`.
- Exactly one PA tier for every integer 0..70.
- `slot` multiplier commutes with summation.
- The IP "6.2" parser gives 20 outs.
- **New regression test:** re-scoring nflverse 2026 Weeks 1–6 (V10) must never equal nflverse `fantasy_points_ppr` for players
  with an INT, a lost fumble or a yardage bonus. This guards against reusing the nflverse column.

---

## 7. Implications for the user's pipeline

- **"Learn the scoring"**: a table lookup keyed by `(site, sport, contest type)`, detected from the entries or salary CSV. Detection
  signals: a `CPT` Roster Position with two rows per player → DK Showdown; a `MVP 1.5x Salary` column and `MVP - 1.5X Points/...`
  → FD single game; `WR/FLEX` and `DST` → DK Classic; `DEF` → FD Classic [VERIFIED: V9 headers]. Then confirm against the
  contest's rules text, or for FD the `scoring_rules` JSON if access is allowed. Do not let an LLM "learn" scoring at run time.
- **"Baseline numbers: game logs"**: re-score raw stat lines with the engine. **Never use nflverse fantasy-point columns** (§3.18).
- **"Fresh forecasts"**: low/likely/high must be **stat quantiles**, not fantasy points. Code turns them into fantasy-point
  distributions, so bonuses and site differences are applied exactly and the forecaster never needs prices or site averages.
- **"Grade it"**: `reconcile.py` checks the engine against site FPTS first, and flips the conflict switches (§6). Only then are
  forecast errors measured. A scoring mismatch is labeled as the failing step.
- **Last-hour step**: rescoring is pure code and cheap, so re-run the simulator over news-affected players only.

## 8. What to verify before go-live (verification checklist)

1. Paste or capture the live text of: DK NFL rules (Classic and Showdown, including the kicker block and the PA note),
   `fanduel.com/rules` (NFL, CFB, NBA, MLB, NHL sections), and the FD single-game rules per sport (cap, slot count, multipliers).
2. Settle the conflict switches with real site FPTS: DK NBA DD/TD stacking, DK MMA total-strikes line, DK NHL 5+ SOG and 3+ BLK
   bonuses, DK MLB caught stealing, DK PGA streak cap per round, FD DEF XP return, FD PA treatment of XPs after non-counted TDs.
3. Confirm the FD 2nd-half and 4th-quarter PA tiers, including the rows above 27.
4. Confirm the DK CFB scoring and roster (8 vs 9 slots), and the FD CFB bonuses, roster and $60,000 cap.
5. Confirm the full DK soccer and DK tennis tables, the DK golf hole-in-one value and tie rule, the DK F1 full table, FD NHL skater
   values and the FD golf table.
6. Confirm the DK and FD rounding modes and the stat-correction windows.
7. After the first slate per sport, run `reconcile.py` against the site's exported player FPTS. Upgrade each `[UNVERIFIED]` value
   it reproduces, and fix any it does not.
