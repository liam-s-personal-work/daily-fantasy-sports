# Data sources that permit automated access, and DFS-site compliance (VERIFIED EDITION)

Research date: 2026-10-21 (NFL 2026 Week 7 opens Thu 2026-10-22). Topic key: `sources_compliance`.
Deliverable for the DFS lineup engine plan (repo `liam-clifford/daily-fantasy-sports`).
This file is the adversarially fact-checked version of `sources_compliance.md`. It replaces it.

**Tag legend (inline, per fact):**
- `[VERIFIED: url]`: re-checked today, 2026-10-21, against the source named. Where the source is a mirror or secondary copy, the tag says so.
- `[CORRECTED: was X; url]`: the original said X, and the source shows something else.
- `[UNVERIFIED]`: could not be confirmed today. **Do not code these as fact.**

---

## 0. How this was checked, and what blocked it (read first)

| Route | Result today |
|---|---|
| `WebSearch` | **No searches ran.** The first call returned "this turn's web search budget is used up (limit: 200 WebSearch calls per turn, shared by every agent in it)". The harness forbids working around the limit through archive, reader or proxy services, so `web.archive.org` was not used. [VERIFIED: WebSearch tool response, 2026-10-21 13:2x UTC] |
| `WebFetch` | `getaddrinfo ENOTFOUND` for `www.draftkings.com`, `www.fanduel.com`, `docs.sleeper.com`, `the-odds-api.com`, `open-meteo.com` and `docs.github.com`, all re-tested today. The original's other ENOTFOUND hosts (`collegefootballdata.com`, `www.weather.gov`, `disneytermsofuse.com`, `www.sports-reference.com`, `gdx.mlb.com`, `nflreadr.nflverse.com`, `weather-gov.github.io`) were not all re-tested, but the proxy rejects them too (next row). `github.com` and `platform.claude.com` **work**. [VERIFIED: WebFetch tool errors, 2026-10-21] |
| `curl` through the session proxy | `curl` prints `000`, and the proxy status log records **`403 connect_rejected`** (policy denial) for `api.sleeper.app`, `api.the-odds-api.com`, `api.open-meteo.com`, `api.weather.gov`, `statsapi.mlb.com`, `api-web.nhle.com`, `www.draftkings.com`, `api.draftkings.com`, `help.draftkings.com`, `www.fanduel.com`, `support.fanduel.com`, `www.balldontlie.io`, `www.pro-football-reference.com`, `data.nflverse.com`, `docs.sleeper.com` and `collegefootballdata.com`. [VERIFIED: `curl $HTTPS_PROXY/__agentproxy/status` recentRelayFailures, 2026-10-21 13:23 UTC] |
| | The original said `www.espn.com`, `www.nfl.com`, `www.reddit.com`, `www.nba.com`, `baseballsavant.mlb.com`, `datagolf.com` and `www.ufcstats.com` "timed out (HTTP 000)". **They are proxy policy denials, not timeouts.** The proxy log shows `403 connect_rejected` for each one, and `web.archive.org`, `nflverse.github.io` and `sportsdataverse.github.io` are also rejected. [CORRECTED: was "timed out (HTTP 000)"; proxy status log 13:00–13:23 UTC] |
| Reachable | `raw.githubusercontent.com` (served with `cache-control: max-age=300`), `github.com` release downloads (302 to `release-assets.githubusercontent.com`), `pypi.org`, `platform.claude.com`, and GitHub code search (MCP tool). [VERIFIED: curl -I, 2026-10-21] |

**How the gaps were filled.** Many claims were upgraded or corrected from official GitHub repositories run by the data owner, such as the CFBD org's SDK docs, Open-Meteo's own website source and nflverse workflow files. Others came from verbatim copies of pages saved in public GitHub repositories, found with GitHub code search and cited as **mirror** or **secondary**. A mirror is not the live page. Rows that depend on one keep `verified_at: null` in the registry until a person reads the live page.

**Confidence tags carried in the register:** `high` = primary source confirmed today, or data downloaded and inspected; `medium` = official-org repo or a verbatim mirror or secondary copy; `low` = recollection.

---

## 1. Status definitions and the decision rule (design, unchanged)

| Status | Meaning | Runtime default |
|---|---|---|
| **ALLOWED** | An official API or data product meant for programmatic use, whose terms (as known) permit our use: one person, personal, non-commercial | ON, once `verified_at` is set |
| **CONDITIONAL** | Allowed only under stated conditions: API key, non-commercial use, attribution, rate limits, non-bulk use, paid tier, or redistributed data whose upstream rights stay with the owner | ON only when every condition is met in config and `verified_at` is set |
| **UNCLEAR** | No affirmative permission found: undocumented or "public" endpoints, ToS not read, or **upstream collected from a source whose terms forbid automation** | **OFF.** The user may enable it after reading the terms (Section 6). |
| **PROHIBITED** | The ToS or policy forbids automated access, or access needs a login, a paywall or anti-bot evasion | **OFF, hard-blocked in code** |

**Rule:** the user's requirement is "only use sites that allow automated access". The engine therefore uses only ALLOWED sources, plus CONDITIONAL sources whose conditions are met. **UNCLEAR counts as not allowed.**

**New sub-rule (an omission in the original): provenance.** Downloading a GitHub release is allowed automated access *to GitHub*. Some nflverse and sportsdataverse files are, however, **collected upstream from ESPN or NFL endpoints**:
- nflverse depth charts are built by `nflverse.espn::espn_depth_charts()`.
- nflverse injuries are built by `nflapi::nflapi_injuries()`.

[VERIFIED: https://github.com/nflverse/nflverse-rosters/blob/main/exec/update-depth-charts.R and exec/update-injuries.R, via GitHub code search]

Each registry row therefore carries an `upstream` field. The user decides once, in config (`allow_upstream_derived: true|false`), whether redistributed copies are acceptable. Default: `true` for nflverse injuries, rosters and schedules, which are the official-report fields, and `false` for ESPN-derived sets. See Section 2.1.

---

## 2. Source register

### 2.1 Football: NFL

**nflverse data releases** (GitHub `nflverse/nflverse-data`), read via **nflreadpy**:
- **Access.** HTTPS download of release assets: `https://github.com/nflverse/nflverse-data/releases/download/<tag>/<file>` returns a 302 to `release-assets.githubusercontent.com`. [VERIFIED: curl -IL, 2026-10-21]
- **Licence.** The repo is labelled **CC-BY-4.0** [VERIFIED: https://github.com/nflverse/nflverse-data]. Its README says: "This repository holds automated data releases for nflverse projects (i.e. all of the data powered/scraped via GitHub Actions)." [VERIFIED: https://raw.githubusercontent.com/nflverse/nflverse-data/master/README.md]
- The nflreadpy README says: "The majority of all nflverse data available (ie all but the FTN data as of July 2025) is broadly licensed as CC-BY 4.0, and the FTN data is CC-BY-SA 4.0". The package code is MIT. [VERIFIED: https://raw.githubusercontent.com/nflverse/nflreadpy/main/README.md]
- The nflreadr README says: "NFL data accessed by this package belong to their respective owners, and are governed by their terms of use." [VERIFIED: https://raw.githubusercontent.com/nflverse/nflreadr/main/README.md]
- **CC BY 4.0 §3(a)(1).** "If You Share the Licensed Material (including in modified form), You must" retain:
  - creator identification;
  - a copyright notice;
  - a licence notice;
  - a disclaimer notice;
  - a URI or hyperlink.

  You must also "indicate if You modified the Licensed Material", and indicate the licence. §3(a)(2) lets you satisfy this "in any reasonable manner… providing a URI or hyperlink". **Note (an omission in the original): these duties apply only when you *share*.** Purely personal use triggers no attribution duty, but the engine still stores licence tags. [VERIFIED: https://raw.githubusercontent.com/nflverse/nflverse-data/master/LICENSE.md]
- **Licence gap (an omission in the original).** The schedules release, `games.csv`, which carries the betting lines, says "Data maintained in Lee Sharpe's nfldata". The `nflverse/nfldata` repo **shows no licence** and does not document where the lines come from. So the licence for the lines is **not stated**, and "majority… CC-BY" does not clearly cover them. Treat them as CONDITIONAL (personal, no redistribution). [VERIFIED: https://github.com/nflverse/nflverse-data/releases/tag/schedules ; https://github.com/nflverse/nfldata (no licence label)]
- `nfl_data_py`: "nfl_data_py has been deprecated in favour of nflreadpy". The repo "was archived by the owner on Sep 25, 2025. It is now read-only." **Do not use it.** [VERIFIED: https://github.com/nflverse/nfl_data_py]
- **nflreadpy 0.1.5**, uploaded 2025-11-19, MIT, requires Python ≥3.10, returns Polars DataFrames. [VERIFIED: https://pypi.org/pypi/nflreadpy/json]
- **Config env vars:** `NFLREADPY_CACHE` (`memory` / `filesystem` / `off`), `NFLREADPY_CACHE_DIR`, `NFLREADPY_CACHE_DURATION`, `NFLREADPY_VERBOSE`, `NFLREADPY_TIMEOUT`, `NFLREADPY_USER_AGENT`. **Code defaults:**
  - `cache_mode=memory`
  - `cache_duration=86400` ("Default is 86400 (24 hours). Set to 0 to always refresh.")
  - `timeout=30`
  - `user_agent="nflverse/nflreadpy {version}"`

  [VERIFIED: https://raw.githubusercontent.com/nflverse/nflreadpy/main/src/nflreadpy/config.py] The original said the 86400 came from a README example only; it is in fact the code default.
- **Design consequence:** the last-hour check must call `update_config(cache_mode="off")`, or set `cache_duration=0`, before reloading injuries and schedules.
- **Loaders:**
  - `load_pbp`, `load_player_stats`, `load_team_stats`, `load_schedules`, `load_players`, `load_rosters`, `load_rosters_weekly`
  - `load_snap_counts`, `load_nextgen_stats` ("advanced stats from nextgenstats.nfl.com"), `load_ftn_charting` ("charted stats from ftnfantasy.com/data")
  - `load_participation` ("participation data (historical)"), `load_draft_picks`, `load_injuries` ("injury statuses and practice participation")
  - `load_contracts` ("historical contract data from OTC"), `load_officials`, `load_combine`, `load_depth_charts`, `load_trades`
  - `load_ff_playerids`, `load_ff_rankings` ("fantasypros rankings"), `load_ff_opportunity`
  - `get_current_season`, `get_current_week`

  [VERIFIED: nflreadpy README] The data sources are nflverse-data, `dynastyprocess/data` and `ffverse/ffopportunity`. [VERIFIED: same]
- Release `player_stats`: "DEPRECATED 2025-08-01: USE `stats_player` OR `stats_team` INSTEAD". [VERIFIED: https://github.com/nflverse/nflverse-data/releases/tag/player_stats]

**Update schedule** (an omission in the original; these are the official statements):

| Data | Documented schedule | Observed today |
|---|---|---|
| Schedules / `games.csv` | "updates very [sic] 5 minutes during the season" | `Last-Modified` was 11:06:14 in the original, **13:06:30 at re-check** [CORRECTED: was a single 11:06 timestamp; the file changes intraday] |
| pbp, stats_player | "nightly basis after each game day (and additionally at specific points on game days)"; raw pbp "usually available within 15 minutes after a game has ended"; "Thursday's `load_pbp()` is the cleanest data" (stat corrections Mon–Wed) | pbp 09:03:59, stats_player_week 09:05:45 |
| FTN charting, PFR snap counts, PFR advstats | "every day at 0, 6, 12, 18 UTC during the season" | FTN 11:01:21, advstats_rec 11:04:14, snap_counts **2026-10-20** 11:01:45 |
| Next Gen Stats | "every night (in the range of 3 am - 5 am ET)" | ngs_receiving 12:34:57 |
| Rosters | "every day at 7AM UTC" (workflow cron `'7 7 * * *'`) | roster_weekly 12:56:34 |
| Depth charts | "every day at 7AM UTC throughout the year" (cron `'7 7 * * *'`) | 12:53:35; in-season `dt` snapshots ≈06:0x UTC **plus** a second one between ≈11:30 and ≈16:40 UTC |
| Injuries | "every day at 7AM UTC throughout the season" (cron `'7 7 * 1,2,9,10,11,12 *'`, Sep–Feb) | 12:53:02 |
| Participation | "provided after all post-season games are completed. It does not update during the season!" | 2025 file exists (Last-Modified 2026-02-10); the 2026 file returns **404** |

[VERIFIED: https://raw.githubusercontent.com/nflverse/nflreadr/main/vignettes/articles/nflverse_data_schedule.Rmd ; https://raw.githubusercontent.com/nflverse/nflverse-rosters/main/.github/workflows/update_injuries.yaml (and update_depth_charts.yaml, update_rosters.yaml) ; HTTP HEAD on release assets 2026-10-21 13:2x UTC]

**Design rule:** never hard-code refresh times. Read `Last-Modified` (and `dt`, for depth charts) and record it as `as_of` on every fact.

**Schemas** (downloaded and inspected today, 2026-10-21):

| Asset | Verified contents |
|---|---|
| `injuries/injuries_2026.parquet` | 1,812 rows × 16 columns: `season, season_type, game_type, team, week, gsis_id, position, full_name, first_name, last_name, report_primary_injury, report_secondary_injury, report_status, practice_primary_injury, practice_secondary_injury, practice_status`. Weeks 1–7; rows per week: 182, 251, 301, 318, 361, 376, **23** (week 7 so far). `report_status` ∈ {`Out`, `Doubtful`, `Questionable`, null}. `practice_status` ∈ {`Did Not Participate In Practice`, `Limited Participation in Practice`, `Full Participation in Practice`, null}. Dictionary: `report_status` = "Player's status for game on official injury report"; `practice_status` = "Player's participation in practice". Upstream: `nflapi::nflapi_injuries()`. [VERIFIED: download; https://raw.githubusercontent.com/nflverse/nflreadr/main/data-raw/dictionary_injuries.csv] |
| `depth_charts/depth_charts_2026.parquet` | 680,140 rows; columns `dt, team, player_name, espn_id, gsis_id, pos_grp_id, pos_grp, pos_id, pos_name, pos_abb, pos_slot, pos_rank`. 249 distinct `dt` from `2026-03-22T06:38:42Z` to `2026-10-21T12:53:27Z`. Dictionary: "the data source has changed after the 2024 season"; from 2025 "depth charts are not assigned a week… each update provides a new ISO8601-formatted timestamp". **Upstream: ESPN** (`nflverse.espn::espn_depth_charts`). [VERIFIED: download; dictionary_depth_charts.Rmd/.csv; exec/update-depth-charts.R] The original said "about 06:00 and 12:00 UTC daily". [CORRECTED: was "about 06:00 and 12:00 UTC daily"; observed ≈06:0x UTC plus one later run at 11:30–16:40 UTC, and the documented cron is once daily at 07:07 UTC] |
| `snap_counts/snap_counts_2026.parquet` | 8,671 rows; columns `game_id, pfr_game_id, season, game_type, week, player, pfr_player_id, position, team, opponent, offense_snaps, offense_pct, defense_snaps, defense_pct, st_snaps, st_pct`; weeks 1–6. [VERIFIED: download] |
| `stats_player/stats_player_week_2026.parquet` | 6,488 rows × **150** columns; weeks 1–6. Includes `targets, target_share, air_yards_share, wopr, racr, carries, passing_air_yards, receiving_air_yards, fantasy_points, fantasy_points_ppr`. **No route or snap columns.** [VERIFIED: download] |
| `schedules/games.csv` | 7,548 rows × **46** columns: `game_id, season, game_type, week, gameday, weekday, gametime, away_team, away_score, home_team, home_score, location, result, total, overtime, old_game_id, gsis, nfl_detail_id, pfr, pff, espn, ftn, away_rest, home_rest, away_moneyline, home_moneyline, spread_line, away_spread_odds, home_spread_odds, total_line, under_odds, over_odds, div_game, roof, surface, temp, wind, away_qb_id, home_qb_id, away_qb_name, home_qb_name, away_coach, home_coach, referee, stadium_id, stadium`. 2026: 272 games; lines present for 121 (weeks 1–8 complete: 16, 16, 16, 16, 15, 14, **14, 14**; week 9+ none). Latest completed game 2026-10-19 (week 6). Future games have 0 non-null `temp`/`wind`. `roof` values seen: `outdoors, open, closed, dome` and empty string. [VERIFIED: download] |
| Schedule dictionary | `spread_line`: "A positive number means the home team was favored by that many points, a negative number means the away team was favored by that many points. This lines up with the result column." `roof`: "One of outdoors, open, closed, dome". `temp`/`wind`: "(for outdoors and open only)"; wind in mph. [VERIFIED: https://raw.githubusercontent.com/nflverse/nflreadr/main/data-raw/dictionary_schedules.csv] Implied team totals (derived): home = `total_line/2 + spread_line/2`, away = `total_line/2 − spread_line/2`. |
| `weekly_rosters/roster_weekly_2026.parquet` | 17,596 rows × **36** columns, including `gsis_id, espn_id, sportradar_id, yahoo_id, rotowire_id, pff_id, pfr_id, fantasy_data_id, sleeper_id, status, status_description_abbr, depth_chart_position, ngs_position`; weeks 1–7. **No DraftKings or FanDuel ID column.** Release text: "Week-level rosters via NFL Shield v2 API, dating back to 2002." [VERIFIED: download; https://github.com/nflverse/nflverse-data/releases/tag/weekly_rosters] |
| Roster status dictionary | `ACT` "On the active roster"; `INA` "Under contract but not on the active roster, inactive"; `RES` "On the reserve list"; `DEV` "On the practice squad"; `CUT` "Cut from the team's roster"; `EXE` "On the comissioner's exempt list"; `PUP`; `SUS` "Player is suspended from the NFL"; `RET`; `TRC`/`TRD`/`TRT` "Player has been released from the practice squad"; `E14`, `NWT`, `RFA`, `RSN`, `RSR`, `TRL`, `UFA`. [VERIFIED: https://raw.githubusercontent.com/nflverse/nflreadr/main/data-raw/dictionary_roster_status.csv] **Omission:** the 2026 data also contains `W04`, which is **not in the dictionary**; the parser must map unknown codes to `unknown`, not crash. [VERIFIED: download] |
| `ftn_charting/ftn_charting_2026.parquet` | 15,904 rows × 29 columns, including `nflverse_game_id, nflverse_play_id, n_offense_backfield, n_defense_box, is_motion, is_play_action, is_screen_pass, is_rpo, is_qb_out_of_pocket, read_thrown, is_catchable_ball, is_contested_ball, is_created_reception, is_drop, n_blitzers, n_pass_rushers`. Play-level, with **no player or route columns** (join to pbp on `nflverse_play_id`). Weeks 1–6. Dictionary: "Data is currently available from 2022 onwards." Licence CC-BY-SA 4.0. [VERIFIED: download; https://raw.githubusercontent.com/nflverse/nflreadr/main/data-raw/dictionary_ftn_charting.csv] |
| `nextgen_stats/ngs_receiving.parquet` | 15,282 rows × 23 columns; seasons **2016–2026** in one file. Includes `avg_cushion, avg_separation, avg_intended_air_yards, percent_share_of_intended_air_yards, targets, avg_yac_above_expectation, player_gsis_id`. 2026 weeks 0–6. [VERIFIED: download] |
| `pfr_advstats/advstats_week_rec_2026.parquet` | 1,485 rows × 17 columns: `game_id, pfr_game_id, season, week, game_type, team, opponent, pfr_player_name, pfr_player_id, rushing_broken_tackles, receiving_broken_tackles, passing_drops, passing_drop_pct, receiving_drop, receiving_drop_pct, receiving_int, receiving_rat` (no routes). [VERIFIED: download] |
| Participation | `pbp_participation_2026.parquet` returns **404**; the 2025 file exists. **There is no free 2026 per-player route or participation data in nflverse.** [VERIFIED: curl -I 2026-10-21; schedule vignette] |
| nflfastR | "updated nightly during the season". [VERIFIED: https://raw.githubusercontent.com/nflverse/nflfastR/master/README.md] |

**Other NFL sources:**

| Source | Status | Key facts | Conf. |
|---|---|---|---|
| NFL Next Gen Stats via nflverse | CONDITIONAL (the NFL owns the data) | As above. nextgenstats.nfl.com terms not read [UNVERIFIED] | high (data) / low (terms) |
| FTN charting via nflverse | CONDITIONAL, **CC-BY-SA 4.0** | As above | high |
| **NFL.com / api.nfl.com ("Shield")** directly | **UNCLEAR → OFF.** Use the nflverse copies, plus Anthropic web search for late news. | ToS not read [UNVERIFIED]. nflverse rosters come "via NFL Shield v2 API" [VERIFIED: release text]. | low |
| **ESPN site/core APIs** (`site.api.espn.com` v2/v3, `sports.core.api.espn.com` v2/v3, `site.web.api.espn.com`, `cdn.espn.com`, `now.core.api.espn.com`, and also **`fantasy.espn.com`**, omitted in the original) | **PROHIBITED → hard OFF** [CORRECTED: was "UNCLEAR, leaning PROHIBITED", based on recollection only] | The unofficial-docs repo says: "'Public' describes endpoint reachability, not permission to collect, reuse, or redistribute data." and "An endpoint being reachable without authentication does not establish permission to automate requests, scrape, store, publish, or redistribute its responses." It also says "no official limits published, but excessive requests may be blocked". [VERIFIED: https://github.com/pseudo-r/Public-ESPN-API README and PROJECT_SCOPE.md] The Disney Terms of Use (which govern ESPN) say users may not "access, monitor or copy, or permit another person or entity to access, monitor or copy, any element of the Disney Products using a robot, spider, scraper or other automated means or manual process without our express written permission". [VERIFIED via mirror: ToS-corpus copies such as https://github.com/RZhu04/TOS-privacy-risk-analysis-tool/blob/main/rawTOS/TheWaltDisneyCompany_DisneyTermsofUse.txt; the live page disneytermsofuse.com was not reachable; copy date unknown] | medium |
| **Sleeper API** (`api.sleeper.app/v1/...`) | **ALLOWED per its docs**; keep `verified_at: null` until the live docs are read | Docs text (mirror): "read-only HTTP API that is free to use"; "No API Token is necessary, as you cannot modify contents via this API."; "stay under 1000 API calls per minute, otherwise, you risk being IP-blocked". `GET /v1/players/nfl`: "Please use this call sparingly, as it is intended only to be used once per day at most… The average size of this query is 5MB." Trending (`/v1/players/<sport>/trending/<type>?lookback_hours=&limit=`): "Please give attribution to Sleeper you are using our trending data" (an omission in the original). [VERIFIED via mirror: https://github.com/einreke/sleeper-scraper-mcp/blob/558283c80fe5afbdb252aaa60975ac24b2523f73/sleeper%20API%20Docs.txt; the same rate-limit text appears in several other repos] Documented player fields: `status`, `injury_status`, `injury_start_date`, `practice_participation`, `depth_chart_position`, `depth_chart_order`, `team`, `position`, `fantasy_positions`, plus `espn_id`, `yahoo_id`, `rotowire_id`, `sportradar_id`, `fantasy_data_id`, `stats_id`. [CORRECTED: was "news_updated"; that field appears in the docs' draft-picks metadata example, not in the players example; same mirror] One third-party note says the docs restrict commercial use [UNVERIFIED]. | medium |
| **The Odds API** | **ALLOWED** (API product, key required) | Official samples: "get a free API key from https://the-odds-api.com"; "Requests used & remaining for your api key"; headers `x-requests-remaining`, `x-requests-used`. "The usage quota cost = [number of markets specified] x [number of regions specified]". "Note only featured markets (h2h, spreads, totals) are available with the odds endpoint" (`/v4/sports/{sport}/odds`). [VERIFIED: https://github.com/the-odds-api/samples-python (README.md, odds.py)] [CORRECTED: the cost formula was UNCONFIRMED recollection; it is in the official sample code] Free-plan size of **500 credits/month** and a paid tier of about **$30/month**: third-party repos agree, but the official pricing page was not read [UNVERIFIED]. Whether the free plan includes player props (per-event endpoint) is [UNVERIFIED]; one third-party plan says it does not. | high (key, cost formula) / low (plan sizes) |
| **SportsDataIO** | ALLOWED under licence (paid) | Pricing and trial terms [UNVERIFIED] | low |
| **Sportradar** | ALLOWED under licence; trial is evaluation-only [UNVERIFIED] | "Sportradar provides a free trial evaluation that provides 1,000 API queries at up to 1 query per second." This comes from an **unofficial** wrapper (`sportradar` 1.6.0, 2021-01-13, author John W. Miller, github.com/johnwmillr/SportradarAPIs). [VERIFIED quote: https://pypi.org/pypi/sportradar/json] [CORRECTED: was described as an official, dated README; it is an unofficial 2021 wrapper] | low–medium |
| **MySportsFeeds** | CONDITIONAL (non-commercial) | "Free for Non-Commercial Use."; "For v1.x feed requests (free non-commercial access available)"; "For v2.0 feed requests (donation required for non-commercial access)". [VERIFIED: https://github.com/MySportsFeeds/mysportsfeeds-python README] | medium |
| Pro Football Reference / Sports Reference | **UNCLEAR → OFF** (nflverse already redistributes PFR snap counts and advanced stats) | Bot policy quoted in code: "Currently we will block users sending requests to our sites more often than twenty requests in a minute". [VERIFIED via secondary: https://github.com/justinpyron/football-rank/blob/main/load_data.py; several other repos also cite 20/min; one cites 10/min, possibly a different SR site] ToS on automated collection and reuse [UNVERIFIED]. | medium (limit) / low (ToS) |
| FantasyPros | **UNCLEAR/PROHIBITED → OFF.** `load_ff_rankings()` (FantasyPros rankings via dynastyprocess) is also OFF, and the forecaster is barred from crowd rankings anyway. | Terms [UNVERIFIED] | low |
| Rotowire | **PROHIBITED → OFF** (paywall). Using `rotowire_id` from the nflverse rosters for ID mapping only is fine. | Terms [UNVERIFIED] | low |

### 2.2 Football: college (CFB)

| Source | Status | Key facts | Conf. |
|---|---|---|---|
| **CollegeFootballData (CFBD) API** | **ALLOWED with free key** (terms page not read) | cfbfastR: "The CollegeFootballData API now requires an API key"; register at https://collegefootballdata.com/key; env var `CFBD_API_KEY`. The `cfbd_*()` wrappers "cover all 84 endpoints in the current CFBD specification (v5.27.1)". [VERIFIED: https://raw.githubusercontent.com/sportsdataverse/cfbfastR/main/README.md] SDK: host `https://api.collegefootballdata.com`, Bearer auth. Endpoints include `GET /lines`, `/games/players`, `/games/teams`, `/games/weather`, `/ppa/players/games`, `/ppa/players/season`, `/player/usage` (season-level: `year`, plus optional `team`, `position`, `player_id`, `exclude_garbage_time`), `/player/returning`, `/plays/stats`, `/stats/player/season`, `/roster`, `/info`, `/info/usage`. Python `cfbd` **5.33.0**, uploaded 2026-10-18. [VERIFIED: https://github.com/CFBD/cfbd-python README and docs/*.md; https://pypi.org/pypi/cfbd/json] **Free tier: "1,000 API calls per month"**. CFBD uses "monthly call limits" rather than per-minute limits, and Patreon tiers give higher monthly limits. [CORRECTED: was "monthly quota UNCONFIRMED"; https://github.com/CFBD/cfbd-net/blob/main/docs/authentication.md (official CFBD org)] **`/games/weather`: "Returns historical and forecast weather data for games. Requires Patreon."** [CORRECTED: was "may be Patreon-only (UNCONFIRMED)"; https://github.com/CFBD/cfbd-python/blob/main/docs/GamesApi.md] `/games/{gameId}/preview/adjusted`: "Requires Patreon Tier 1." No Patreon note on `/player/usage`. **`GET /info` "Returns the authenticated user's Patreon level and remaining API calls"**, so the engine can check its quota before a run. [VERIFIED: docs/GamesApi.md, docs/InfoApi.md] | high |
| **cfbfastR / sportsdataverse-data releases** | **CONDITIONAL** for CFBD- and NCAA-derived sets; **UNCLEAR → OFF** for ESPN-derived `load_espn_cfb_*` (Disney ToU, Section 2.1). *Decided 2026-10-21: accepted for personal, non-commercial use; see open question 5.* | "pull pre-built season datasets from the sportsdataverse-data releases — no API key, no scraping". Families: Classic (`load_cfb_pbp`, `load_cfb_schedules`, `load_cfb_rosters`, `load_cfb_teams`); ESPN `load_espn_cfb_*()` (27: pbp, schedules, team/player box, drives, game rosters, linescores, **betting**, play participants, FPI and more); ratings and recruiting; NCAA `load_ncaa_mfb_*()` (10). [VERIFIED: cfbfastR README] Licence of the releases: CC BY 4.0 per the sportsdataverse-data README (checked 2026-10-21 during the pre-merge review). | high (mechanics) / low (licence) |
| CFB availability reports | Use Anthropic web search on official conference and school domains | Whether national or conference availability reports exist in 2026, and where they are published [UNVERIFIED] | low |

**CFBD budget design (new, from the verified quota).** 1,000 calls/month is about 33 calls/day.
- Cache every response on disk, keyed by endpoint and parameters.
- Pull season and history from sportsdataverse releases, not from CFBD.
- Per slate, call `/games/players?year=&week=` once per completed week, `/lines?year=&week=` once, and `/roster?year=&team=` once per team per season.
- Check `/info` remaining calls before any run, and fail closed below a reserve, for example 50.

### 2.3 Other sports

| Source | Sport | Status | Key facts | Conf. |
|---|---|---|---|---|
| **nba_api** → stats.nba.com | NBA | **UNCLEAR → OFF** | `nba_api` 1.11.4, uploaded 2026-02-20, MIT. README: "NBA.com has a Terms of Use regarding the use of the NBA's digital platforms." [VERIFIED: https://pypi.org/pypi/nba_api/json ; https://github.com/swar/nba_api README] Datacenter-IP blocking and browser-like headers [UNVERIFIED]. Header spoofing would break our runtime rule anyway. | high (quote) / low (rest) |
| **balldontlie** | NBA, **NFL, MLB** | ALLOWED (API product, key) | Official SDK `balldontlie` 0.1.6 (2024-12-21, MIT): "Official Python SDK for the BALLDONTLIE API. Access NBA, NFL, and MLB statistics and data." Uses `BalldontlieAPI(api_key=...)` and exposes `RateLimitError`. [VERIFIED: https://pypi.org/pypi/balldontlie/json] Tiers (free about 5 req/min with teams, players and games only; about $9.99/month; about $39.99/month for box scores, props and odds): third-party notes agree [UNVERIFIED]. The original's GitHub README path (`balldontlie-api/python`, branch main) returned 404 today. | medium (exists, key, sports) / low (tiers) |
| hoopR / sportsdataverse NBA | NBA, MBB | UNCLEAR → OFF (ESPN and NBA upstream). *Decided 2026-10-23: the ESPN player box scores (`espn_nba_player_boxscores` release files) are accepted on the same terms as the college sets (question 5): personal, non-commercial, not redistributed. NBA-Stats-derived sets stay off.* | — | medium |
| NBA official injury report | NBA | UNCLEAR; only through a robots-respecting fetcher | [UNVERIFIED] | low |
| **MLB Stats API** (`statsapi.mlb.com`) | MLB | **CONDITIONAL** (individual, non-commercial, non-bulk) | `MLB-StatsAPI` 1.9.0 (2025-04-04). README: "Use of MLB data is subject to the notice posted at http://gdx.mlb.com/components/copyright.txt." [VERIFIED: https://github.com/toddrob99/MLB-StatsAPI README; PyPI] Notice wording: "The accounts, descriptions, data and presentation in the referring page (the "Materials") are proprietary content of MLB Advanced Media, L.P ("MLBAM"). Only individual, non-commercial, non-bulk use of the Materials is permitted and any other use of the Materials is prohibited without prior written authorization from MLBAM. Authorized users of the Materials are prohibited from using the Materials in any commercial manner other than as expressly authorized by MLBAM." [CORRECTED: was low-confidence recollection of the first sentence only; verified via secondary copies https://github.com/jasonlttl/gameday-api-docs README and https://github.com/raycast/extensions/blob/main/extensions/mlb-scores/README.md; the live page could not be read] | high (pointer) / medium (wording) |
| Baseball Savant (Statcast) | MLB | UNCLEAR → OFF | pybaseball: "This package scrapes Baseball Reference, Baseball Savant, and FanGraphs"; CSV docs at https://baseballsavant.mlb.com/csv-docs. [VERIFIED: https://github.com/jldbc/pybaseball README] | high (mechanics) / low (terms) |
| FanGraphs / Baseball Reference | MLB | UNCLEAR → OFF | [UNVERIFIED] | low |
| **NHL web API** (`api-web.nhle.com`, `api.nhle.com/stats/rest`) | NHL | **UNCLEAR → OFF** until the NHL terms are read | "This document aims to serve as an unofficial reference for the NHL APIs"; "there appears to be *two* primary sources for official NHL APIs. (api-web.nhle.com and api.nhle.com/stats/rest)". [VERIFIED: https://github.com/Zmalski/NHL-API-Reference README] `nhl-api-py` 3.3.0 (2026-05-11, Apache-2.0). [VERIFIED: PyPI] The claim that the NHL publishes no official injury report is [UNVERIFIED]. | high (quotes) / low (terms) |
| **DataGolf API** | PGA | ALLOWED under paid subscription [UNVERIFIED] | Wrapper `data-golf` 0.5.1 (2024-06-26): "This is an unofficial API lib, and it is not affiliated with DataGolf in any way." [VERIFIED: https://pypi.org/pypi/data-golf/json] Paid-membership requirement [UNVERIFIED]. | low |
| PGA Tour / ShotLink | PGA | UNCLEAR → OFF | [UNVERIFIED] | low |
| **UFCStats.com** live | MMA | UNCLEAR → OFF | Terms not read [UNVERIFIED] | low |
| `Greco1899/scrape_ufc_stats` (GPL-3.0 code) | MMA | UNCLEAR (data rights) → OFF by default | Scheduled refresh: "will run daily via Cloud Scheduler". The repo serves `ufc_event_details.csv` (header `EVENT,URL,DATE,LOCATION`; latest row is the 2026-10-17 event), `ufc_fight_details.csv`, `ufc_fight_results.csv`, `ufc_fight_stats.csv`, `ufc_fighter_details.csv` and `ufc_fighter_tott.csv`. **The README lists `ufc_events.csv`, but that path returns 404; use `ufc_event_details.csv`.** [VERIFIED: https://github.com/Greco1899/scrape_ufc_stats (licence GPL-3.0); raw file checks 2026-10-21] | high (repo facts) / low (rights) |
| StatsBomb open data | Soccer | CONDITIONAL (attribution) | "If you publish, share or distribute any research, analysis or insights based on this data, please state the data source as StatsBomb and use our logo". [VERIFIED: https://github.com/statsbomb/open-data README] | high |

### 2.4 Weather

| Source | Status | Key facts | Conf. |
|---|---|---|---|
| **NWS `api.weather.gov`** | **ALLOWED** (US government public API) | Flow: `/points/{lat},{lon}` → returned `/gridpoints/{wfo}/{x},{y}/forecast/hourly`. For 403 errors: "Make sure your program is including a `User-Agent` header… We recommend setting the value to something that identifies your application and includes a contact email." "In the future we will replace the User-Agent requirement with a more typical API key system." "The point mappings don't change very often, so you can cache the result of the /points request". "We send back Cache-Control headers… and Last-Modified headers". "Please don't use cache busting techniques like random numbers in the query string. **Requests like this with query parameters that aren't recognized by the API will return a 400 (Bad Request) response.**" (the last sentence was omitted in the original). The FAQ gives **no rate-limit number**. OpenAPI spec at `https://api.weather.gov/openapi.json`. [VERIFIED: https://raw.githubusercontent.com/weather-gov/api/master/general-faqs.md] | high |
| **Open-Meteo** | **CONDITIONAL** (non-commercial; CC BY 4.0) | README: "offers free access for non-commercial use. No API key is required"; "Free for non-commercial use with data under Attribution 4.0 International (CC BY 4.0)"; "We do not restrict access, but ask for fair use. If your application exceeds 10'000 requests per day, please contact us. We reserve the right to block applications and IP addresses that misuse our service."; "For commercial use of Open-Meteo APIs, please contact us." [VERIFIED: https://raw.githubusercontent.com/open-meteo/open-meteo/main/README.md] **Terms page: "Less than 10'000 API calls per day, 5'000 per hour and 600 per minute."; "Rate limits are applied by IP addresses and application."** Commercial use includes "Websites or apps that have subscriptions or display advertisements"; non-commercial includes "Personal home automation purposes". [CORRECTED: was "about 10,000/day before contact" only; https://github.com/open-meteo/open-meteo-website/blob/main/src/routes/en/terms/+page.svelte (Open-Meteo's own site source)] | high |
| nflverse `games.csv` | CONDITIONAL | `roof`, `surface`, and post-game `temp`/`wind` "(for outdoors and open only)". `temp`/`wind` are **null for all future games**, so they are not a forecast. [VERIFIED: download + dictionary] | high |
| CFBD `/games/weather` | **CONDITIONAL: Patreon only** | "Requires Patreon." [CORRECTED: was "ALLOWED (tier UNCONFIRMED)"; https://github.com/CFBD/cfbd-python/blob/main/docs/GamesApi.md] Use NWS or Open-Meteo for free CFB weather. | high |

### 2.5 News, beat reporting, inactives

**Anthropic web search tool** (server-side, Claude API). **ALLOWED**: Anthropic runs the search, and we crawl nothing.
- **Versions:**
  - `web_search_20250305`: "basic web search".
  - `web_search_20260209`: "adds dynamic filtering".
  - `web_search_20260318`: "adds response inclusion control for agentic workflows".

  "Dynamic filtering is available with Claude 4.6 and later models and Claude Mythos Preview."
- **Omission in the original:** on `web_search_20260209`+, `allowed_callers` "defaults to `["code_execution_20260120"]`". To call search directly, set `allowed_callers: ["direct"]`. "Models that don't support programmatic tool calling require this setting. Without it, the API returns a 400 error". Web search "is not available on Amazon Bedrock"; Google Cloud has basic search only.
- **Parameters:** `max_uses`, `allowed_domains` **or** `blocked_domains`. "If a request includes both, the API returns a 400 error". Entries are bare domains, with an optional path and no scheme. `user_location` takes `type: approximate`, `city`, `region`, `country` and `timezone`; an unsupported country code returns 400.
- **Result fields:** `url`, `title`, `page_age`, `encrypted_content`. The `encrypted_content` "must [be passed] back in multi-turn conversations", or the request gets a 400.
- **Citations:** "Citations are always enabled for web search". `cited_text` holds "Up to 150 characters". "When displaying API outputs directly to end users, citations must be included to the original source."
- **Errors** come back inside an HTTP 200: `too_many_requests`, `invalid_tool_input`, `max_uses_exceeded`, `query_too_long`, `request_too_large`, `unavailable`. A search with no matches returns empty `content`, not an error. Long turns can return `stop_reason: "pause_turn"`.
- **Org admins:** web search "is enabled for your organization unless an administrator has disabled it in the Claude Console, where they can also restrict which domains it searches". When it is disabled, the request fails with a 400 `invalid_request_error`.
- **Pricing:** "**$10 per 1,000 searches**, plus standard token costs for search-generated content. **Web search results retrieved throughout a conversation are counted as input tokens**, in search iterations executed during a single turn and in subsequent conversation turns." "Each web search counts as one use… If an error occurs during web search, the web search will not be billed." The Batches API throttles web search per org.
- [VERIFIED: https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool]

**Anthropic web fetch tool** (server-side). **ALLOWED, and it enforces robots.txt.**
- **Versions:**
  - `web_fetch_20250910`: "basic fetch".
  - `web_fetch_20260209`: "dynamic filtering only".
  - `web_fetch_20260309`: "dynamic filtering and cache bypass", which adds `use_cache`.
  - `web_fetch_20260318`: dynamic filtering plus `response_inclusion`.

  [CORRECTED: the original listed 20260309 as only "adds use_cache"; it also carries dynamic filtering]
- **Parameters:** `max_uses` ("Failed fetches count against the limit… There is currently no default limit"), `allowed_domains`, `blocked_domains`, `url_sources`, `citations` (**off by default** for fetch; set `{"enabled": true}`), and `max_content_tokens` (approximate; applies to text, not PDFs).
- **Cache:** results may be cached. Set `"use_cache": false` (20260309+) to "bypass the cache and fetch fresh content".
- **URL validation:** "Claude can only fetch URLs that have previously appeared in the conversation: URLs in user messages, URLs in client-side tool results, and URLs from previous web search or web fetch results." It "cannot fetch URLs that appear only in Claude's own output **or only in the system prompt**" (an omission in the original).
- **Content:** it "does not support websites dynamically rendered with JavaScript".
- **Errors:**
  - `url_too_long`: "(250 characters)";
  - `url_not_allowed`: "URL blocked by domain filtering rules (including your organization's settings) or by Anthropic-side restrictions, such as private addresses, `robots.txt`, and URLs that appear to contain a credential you did not provide";
  - `url_not_in_prior_context`, `url_not_accessible`, `too_many_requests`;
  - `unsupported_content_type`: "(only text, HTML, and PDF)";
  - `max_uses_exceeded`, `unavailable`.
- **Pricing:** "no additional charges beyond standard token costs". "Average web page (10 kB): ~2,500 tokens".
- **Availability:** web fetch is "not currently available on Amazon Bedrock or Google Cloud".
- [VERIFIED: https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-fetch-tool]

**Other news sources:**

| Source | Status | Key facts | Conf. |
|---|---|---|---|
| Reddit Data API (PRAW 8.0.3, 2026-08-12) | CONDITIONAL → OFF by default | PRAW "internally follows all of Reddit's API rules". [VERIFIED: https://github.com/praw-dev/praw README; PyPI] 100 QPM per OAuth client, the 2025 approval policy, and the ban on unauthenticated scraping [UNVERIFIED] | low |
| X (Twitter) API | CONDITIONAL (paid) → OFF | "Bearer Token \| Read-only (search, lookup) \| `BEARER_TOKEN`". [VERIFIED: https://github.com/xdevplatform/samples README] Pricing and the scraping ban [UNVERIFIED] | medium (token) / low (price) |
| Google News RSS | UNCLEAR → OFF; use Anthropic web search | [UNVERIFIED] | low |
| Team and league RSS / press pages | CONDITIONAL: robots-respecting fetcher, one page per item | Terms vary [UNVERIFIED] | low |
| NFL game-day inactives | No free ALLOWED structured feed found. nflverse `roster_weekly.status` (`INA` etc.) is weekly, not the game-day list. Use web search on official team and league domains, or a paid licensed feed. | Timing "about 90 minutes before kickoff" [UNVERIFIED] | medium (no feed) / low (timing) |

### 2.6 Infrastructure and DFS sites

| Source | Status | Notes | Conf. |
|---|---|---|---|
| GitHub (release hosting) | ALLOWED | Release downloads redirect to `release-assets.githubusercontent.com`; raw files are served with `cache-control: max-age=300`. [VERIFIED: curl -I 2026-10-21] The unauthenticated REST limit of "60 req/hour", and the claim that release downloads do not count against it, are [UNVERIFIED]: this session's proxy authenticates GitHub API calls, with a 15,000 limit observed. | high (mechanics) / low (number) |
| **DraftKings and FanDuel websites and internal APIs** (`*.draftkings.com` incl. `api.draftkings.com` draftables/rules, `*.fanduel.com`) | **PROHIBITED for this tool (policy decision)** | The user downloads the CSV and uploads the result; the engine never calls any DK or FD endpoint. Both hosts were proxy-blocked today anyway. See Part 2. | policy |

---

## 3. Data need → source map (free-first)

| Data need | NFL | CFB | NBA | MLB | NHL | PGA | MMA |
|---|---|---|---|---|---|---|---|
| Schedules | nflverse `games.csv` (5-min refresh) | CFBD `/games`; sportsdataverse (classic) | balldontlie | MLB Stats API (CONDITIONAL) | entries CSV + web search | entries CSV + web search (DataGolf paid) | entries CSV + web search |
| Rosters / IDs | nflverse `roster_weekly` (gsis, espn, sleeper, yahoo, rotowire, pff, pfr, sportradar, fantasy_data; **no DK/FD IDs**) | CFBD `/roster` | balldontlie | MLB Stats API | entries CSV | entries CSV | entries CSV |
| Depth charts | nflverse `depth_charts` (`dt` snapshots; **ESPN upstream**, gated by `allow_upstream_derived`) + Sleeper `depth_chart_order` | CFBD `/player/usage` as a proxy (season-level) | web search | MLB lineups (game feed) | web search (lines, goalies) | — | — |
| Game logs and usage | nflverse `stats_player_week`, `snap_counts`, NGS, FTN, pbp (red-zone usage). **No free 2026 per-player routes.** | CFBD `/games/players`, `/ppa/players/games` | balldontlie (box scores may be paid, UNVERIFIED) | MLB Stats API box scores | — | DataGolf (paid) | Greco1899 CSVs (rights UNCLEAR) |
| Official injury and practice reports | nflverse `injuries` (`report_status`, `practice_status`; daily refresh) | web search (availability reports) | web search / robots-respecting fetch | MLB transactions (Stats API) | web search | web search (WDs) | web search |
| Inactives / late scratches | Anthropic web search on official domains; paid feed upgrade | web search | web search | MLB lineup cards (Stats API) | web search | web search | web search |
| Betting lines | nflverse `spread_line`/`total_line`/moneylines (weeks 7–8 present; licence not stated) → The Odds API for multi-book/live | CFBD `/lines` | The Odds API | The Odds API | The Odds API | The Odds API (outrights) | The Odds API (h2h) |
| Player props | The Odds API per-event endpoint (free-tier availability UNVERIFIED) | sparse | The Odds API | The Odds API | The Odds API | — | — |
| Weather | nflverse `roof` + NWS hourly; Open-Meteo for non-US venues | NWS / Open-Meteo (CFBD weather is Patreon only) | n/a | NWS / Open-Meteo | n/a | Open-Meteo / NWS | n/a |
| News | Anthropic web search + web fetch | same | same | same | same | same | same |

**Paid upgrade path** (one vendor is enough):
- SportsDataIO or Sportradar for licensed injuries, inactives, depth charts, lineups and goalies, and props.
- A paid tier of The Odds API.
- DataGolf for PGA.
- A CFBD Patreon tier for weather and a larger quota.

---

## 4. Recommended minimal compliant stack per sport

**NFL (free).**
- `nflreadpy` (cache off, or `cache_duration=0`, for the last-hour reload) for: schedules and lines, `roster_weekly`, `injuries`, `snap_counts`, `stats_player_week`, `pbp`, `ftn_charting`, `nextgen_stats`, `pfr_advstats`. Add `depth_charts` only if `allow_upstream_derived` covers ESPN-sourced data.
- NWS and Open-Meteo for weather.
- Anthropic web search and web fetch for news and inactives.
- Optional: The Odds API free key.

Freshness, re-checked 2026-10-21 (UTC Last-Modified): injuries 12:53:02 (week 7 has 23 rows so far), depth_charts 12:53:35 (249 snapshots since 2026-03-22), roster_weekly 12:56:34, ngs_receiving 12:34:57, ftn 11:01:21, pfr advstats_rec 11:04:14, **games.csv 13:06:30** (it was 11:06:14 at the original check; the file refreshes every 5 minutes), stats_player_week 09:05:45, pbp 09:03:59, snap_counts **2026-10-20** 11:01:45. [VERIFIED: HTTP HEAD]

**CFB (free).**
- CFBD free key: **1,000 calls/month**, so cache aggressively and check `/info` first.
- sportsdataverse classic/NCAA releases for bulk history.
- NWS and Open-Meteo for weather.
- Web search for availability and depth changes.

**NBA.**
- balldontlie key (its free tier may be teams, players and games only, UNVERIFIED).
- Web search for the injury report and starters.
- The Odds API.

nba_api stays OFF.

**MLB (postseason now).**
- MLB Stats API under the MLBAM notice (individual, non-commercial, non-bulk; one slate's worth of calls, cached).
- NWS and Open-Meteo.
- The Odds API.

Savant stays OFF.

**NHL.** No ALLOWED structured free source. Use the entries CSV, web search (lines, starting goalies) and The Odds API.

**PGA.** DataGolf (paid), or the free fallback: entries CSV, web search, Open-Meteo wind and Odds API outrights.

**MMA.** Entries CSV, web search and Odds API h2h. Add the Greco1899 CSVs only if the user accepts UNCLEAR rights.

**Other sports.** Entries CSV, web search and The Odds API; per the user's pipeline, the baseline leads.

---

## 5. Runtime rule for agents' web use (enforce in code)

1. **Search only through Anthropic's `web_search`.**
   - Pin `max_uses`.
   - Pass a per-sport `allowed_domains` allowlist, which cannot be combined with `blocked_domains`.
   - Store `url`, `page_age` and `cited_text` as provenance on every fact.
   - Show citations to the user.
   - Use `web_search_20260209`+ with dynamic filtering on Claude 4.6+ models to cut tokens. On other models, set `allowed_callers: ["direct"]`.
2. **Fetch only through Anthropic's `web_fetch` or the engine's own `PoliteFetcher`.**
   - Treat `url_not_allowed` (robots.txt) as final.
   - Use `use_cache: false` in the last hour.
   - Turn on `citations: {enabled: true}`, since it is off by default.
   - Put the URLs to fetch in a **user** message or a client tool result, never only in the system prompt.
   - `PoliteFetcher` must:
     - check `robots.txt` (`urllib.robotparser`);
     - send `User-Agent: dfs-engine/<ver> (<contact email>)`;
     - make ≤1 request/s/host, with backoff on 429/5xx;
     - honour `Cache-Control` / `Retry-After`;
     - send no cache-busting parameters (NWS returns 400 for unknown parameters);
     - fetch one page per item, with no crawling.
3. **Hard prohibitions (exceptions in code):**
   - no logins and no user cookies;
   - no paywalls;
   - no CAPTCHA, anti-bot or IP-rotation evasion, and no faked browser headers;
   - no `*.draftkings.com` or `*.fanduel.com`;
   - no direct ESPN (PROHIBITED), Rotowire, FantasyPros, Sports Reference, NFL.com, stats.nba.com, X or Reddit, unless the registry row is changed with `verified_at`.
4. **Registry gate.** Refuse any adapter whose row is `UNCLEAR` or `PROHIBITED`, or has no `verified_at`. Also refuse `upstream: espn` rows unless `allow_upstream_derived` lists ESPN.
5. **Licence hygiene.**
   - Store licence tags: nflverse CC-BY-4.0 (except schedules/lines: licence not stated), FTN CC-BY-SA-4.0, Open-Meteo CC-BY-4.0, Sleeper trending (attribution requested).
   - Never republish raw data.
   - Keep the DK/FD files out of git (`data/entries/` in `.gitignore`).
6. **Fetched text is untrusted.** Extract only structured facts (player, status, source, timestamp). Never follow instructions found in page text.
7. **Forecaster information wall.** Drop all of these before facts reach the "fresh forecast" agent:
   - `Salary`;
   - **`AvgPointsPerGame` from the DK file's embedded pool** (an omission in the original; see §7);
   - Sleeper and FantasyPros projections or rankings;
   - `fantasy_points` / `fantasy_points_ppr` from nflverse.
8. **Budget.**
   - Last-hour check: about 1 search per pick plus 1 per game. For 9 picks on a 5-game slate that is 14 searches, or **$0.14 in search fees only**. **Search results are also billed as input tokens**, in that turn and every later turn that carries them. Measure `usage.server_tool_use.web_search_requests` and `usage.input_tokens` per call; no token figure is assumed here. [CORRECTED: was "costs about $0.14" with tokens left out; web-search-tool docs]
   - Research phase: about 3 searches per game.
   - Cap web fetches with `max_uses` and `max_content_tokens`; an average page is about 2,500 tokens.

---

## 6. Verification checklist (go-live gate) and registry config

Pages a person must read (or paste in) before a row gets `verified_at`:

| Source | Pages to read |
|---|---|
| DraftKings | `https://www.draftkings.com/help/terms`; `https://www.draftkings.com/help/rules`; `https://www.draftkings.com/robots.txt` |
| FanDuel | `https://www.fanduel.com/terms`; `https://www.fanduel.com/rules`; robots.txt; support articles on CSV upload and scripts |
| Sleeper | `https://docs.sleeper.com/` (the mirror text is verified; the live page is not) |
| CFBD | `https://collegefootballdata.com/` terms (the quota is verified via the CFBD org repo) |
| The Odds API | `https://the-odds-api.com/terms-and-conditions.html`; pricing; `https://the-odds-api.com/liveapi/guides/v4/#usage-quota-costs` |
| balldontlie | site terms and pricing |
| MLB | `http://gdx.mlb.com/components/copyright.txt` (the wording is verified via secondary copies) |
| NHL | NHL terms of service (URL UNVERIFIED) |
| NBA | `https://www.nba.com/termsofuse` |
| ESPN | `https://disneytermsofuse.com/english/` (mirror verified; status already PROHIBITED) |
| Sports Reference | `https://www.sports-reference.com/bot-traffic.html`; `/termsofuse.html` |
| Reddit / X / DataGolf | data API terms / developer agreement / api-access page |

Registry (`config/sources.yaml`) shape, updated with the verified facts:

```yaml
- id: nflverse
  sports: [nfl]
  status: CONDITIONAL
  conditions: [attribution_cc_by_4, ftn_cc_by_sa_4, personal_noncommercial, no_redistribution]
  access: github_release
  base: https://github.com/nflverse/nflverse-data/releases/download/
  upstream: {injuries: nfl_api, rosters: nfl_shield_v2, depth_charts: espn, schedules_lines: nfldata_unlicensed}
  refresh_doc: {schedules: 5min, injuries: "07:07 UTC daily", depth_charts: "07:07 UTC daily", rosters: "07:07 UTC daily",
                ftn_snaps_pfr: "00/06/12/18 UTC", ngs: "03-05 ET nightly", pbp_stats: "nightly + game days"}
  freshness_source: http_last_modified     # never trust the documented schedule
  client: {lib: nflreadpy==0.1.5, cache_mode: "off"}   # code default is memory cache, 86400 s
  tos_url: https://github.com/nflverse/nflverse-data/blob/master/LICENSE.md
  verified_at: 2026-10-21
  confidence: high
- id: nws
  sports: [nfl, cfb, mlb, pga]
  status: ALLOWED
  conditions: [user_agent_with_contact, no_unknown_query_params, honour_cache_control]
  base: https://api.weather.gov/
  rate_limit: {per_host_rps: 1}           # FAQ publishes no number; self-imposed
  tos_url: https://github.com/weather-gov/api/blob/master/general-faqs.md
  verified_at: 2026-10-21
  confidence: high
- id: open_meteo
  status: CONDITIONAL
  conditions: [noncommercial, attribution_cc_by_4]
  rate_limit: {per_minute: 600, per_hour: 5000, per_day: 10000}   # stay strictly below ("Less than")
  tos_url: https://open-meteo.com/en/terms
  verified_at: 2026-10-21
  confidence: high
- id: cfbd
  status: ALLOWED
  conditions: [api_key, bearer_auth, monthly_quota]
  base: https://api.collegefootballdata.com
  quota: {calls_per_month: 1000, check_endpoint: /info, reserve: 50}
  patreon_only: [/games/weather, /games/{gameId}/preview/adjusted]
  verified_at: null          # quota verified via CFBD org repo; site terms not read
  confidence: medium
- id: anthropic_web_search
  status: ALLOWED
  conditions: [show_citations, max_uses_cap, allowed_domains_xor_blocked]
  tool: web_search_20260318        # or web_search_20250305 + allowed_callers: [direct] on pre-4.6 models
  cost_usd_per_1000: 10            # plus result tokens billed as input
  verified_at: 2026-10-21
- id: anthropic_web_fetch
  status: ALLOWED
  conditions: [robots_enforced_by_anthropic, url_must_be_in_user_msg_or_tool_result]
  tool: web_fetch_20260318         # use_cache: false in last hour; citations: {enabled: true}
  verified_at: 2026-10-21
- id: sleeper
  status: ALLOWED
  conditions: [under_1000_calls_per_min, players_endpoint_daily, trending_attribution]
  verified_at: null          # docs verified only via mirror; engine keeps it OFF until set
  confidence: medium
- id: the_odds_api
  status: ALLOWED
  conditions: [api_key, credit_budget]
  cost_rule: markets_x_regions     # per /odds call (official sample)
  free_credits_per_month: null     # 500 is UNVERIFIED
  verified_at: null
- id: espn_unofficial
  status: PROHIBITED         # Disney ToU robot/spider/scraper clause (mirror)
- id: draftkings_site
  status: PROHIBITED         # hard block, never fetched
- id: fanduel_site
  status: PROHIBITED
```

---

# Part 2: DFS-site compliance (DraftKings, FanDuel)

## 7. What could be read

- **No DK or FD Terms of Use, rules page or robots.txt could be read today.** WebFetch failed with ENOTFOUND, and the proxy denied `www/api/help.draftkings.com` and `www/support.fanduel.com` with 403. [VERIFIED: tool errors + proxy log] Every DK/FD clause below is [UNVERIFIED].
- **DK `DKEntries` export: a real file was verified.** It is NBA, from March 2026, in a public GitHub repo. [VERIFIED: https://raw.githubusercontent.com/troyseanwilliams-hash/Fantasy-Lineup-Optimizer/main/attached_assets/DKEntries_(17)_1773687635103.csv]
  - Header (row 1, 14 cells): `Entry ID,Contest Name,Contest ID,Entry Fee,PG,SG,SF,PF,C,G,F,UTIL,,Instructions`.
  - 150 entry rows, all `NBA $25K mini-MAX [150 Entry Max]`, contest `188895738`, fee `$0.50`. Slot cells look like `Name (ID)`, e.g. `Josh Giddey (42297980)`.
  - Instructions (column N, rows 2–6):
    - "1. Column A lists all of your contest entries for this draftgroup"
    - "2. Your current lineup is listed next to each entry (blank for reservations)"
    - "3. You can change the lineup for an entry by entering the IDs of the players in the row next to that entry"
    - "4. Use data from the Name+ID column or the ID column; you cannot use just the player's name"
    - "5. For faster processing only include entries you are changing in the file you upload"
  - **Omission in the original:** the same file **embeds the player pool**. Row 8, from column N, holds the header `Position,Name + ID,Name,ID,Roster Position,Salary,Game Info,TeamAbbrev,AvgPointsPerGame`. Pool rows are 22 cells wide, e.g. `PF,Jalen Johnson (42297962),Jalen Johnson,42297962,PF/F/UTIL,10900,ORL@ATL 03/16/2026 07:00PM ET,ATL,50.91`. In this file there are 215 pool rows next to the 150 entry rows. **`AvgPointsPerGame` is a site average and must be stripped before the forecaster.** `Game Info` (`AWAY@HOME MM/DD/YYYY HH:MMPM ET`) gives lock times.
- **Blank lineup-upload template (DK):** row 1 holds the slot labels only, with no Entry columns. The instruction reads "5. You can create up to 500 lineups per file". The pool header sits on row 8. [VERIFIED via copy: https://raw.githubusercontent.com/scott-mccartney/dk-lineup-generator/HEAD/templates/nhl.csv (NHL, Nov 2024)] The upload page `https://www.draftkings.com/lineup/upload` is cited in draftfast's code. [VERIFIED: https://raw.githubusercontent.com/BenBrostoff/draftfast/master/draftfast/csv_parse/uploaders.py line 46]
- **Locked players:** a cell such as `Josh Allen (44220217) (LOCKED)` appears in a DK late-swap entries file. [VERIFIED via fixture: https://raw.githubusercontent.com/JonnyRank/nfl-dfs-optimizer/HEAD/tests/fixtures/public/DKEntriesClassicLateSwap.csv]

## 8. Reported clauses (all [UNVERIFIED]; paste the verbatim text before go-live)

| Topic | DraftKings (reported substance) | FanDuel (reported substance) | Where to verify |
|---|---|---|---|
| Scripts, bots, automated access | Unauthorised scripts and automated means of accessing the site, or of entering, creating or editing lineups, are prohibited. DK and FD moved against "scripting" in early 2016, and the CSV upload became the sanctioned bulk path. [UNVERIFIED] Secondary research notes in public repos quote a DK Network ToU clause: "You may not intercept, mine, harvest, or otherwise collect any Content or information from the Website or Services, including, without limitation, through use of any software or automated means." They also quote a DK Pick6 clause barring "bots, scripts, artificial intelligence, programs, parsers, spiders, screen scrapers, or tools… to automatically enter Contests". [UNVERIFIED: secondary only, e.g. https://github.com/Beexly/Sports docs/research/2026-09-28/orchestration/salary-import-lanes.md] | Same direction; only FD-provided tools, such as CSV upload and edit [UNVERIFIED] | DK `/help/terms`; FD `/terms` |
| Data scraping | Prohibited without permission [UNVERIFIED] | same [UNVERIFIED] | same |
| Third-party research tools | Tools that do not interact with the site are tolerated [UNVERIFIED] | same [UNVERIFIED] | same + help centre |
| CSV upload | DK provides it [VERIFIED, §7]; the blank template allows 500 lineups per file [VERIFIED via copy, §7] | FD provides CSV upload and edit [UNVERIFIED here; see the sibling `site_formats` research] | upload pages |
| Max entries | In the contest name, e.g. `[150 Entry Max]` [VERIFIED, §7]; also `[Single Entry]` and others (sibling research). One account per person [UNVERIFIED]. | Per contest [UNVERIFIED] | contest pages |
| State regulation | Some states (e.g. NY, MA) require operators to bar unauthorised scripts and flag highly experienced players [UNVERIFIED] | same | state regulators |

## 9. What this tool MUST and MUST NOT do (design, unchanged except where noted)

**MUST:**
1. Take the user's own downloaded file as input: DK `DKEntries.csv` (which embeds the pool, §7) or a salaries CSV; FD's entries or players CSV.
2. Output **one CSV in the site's own upload layout**, which the **user uploads by hand**. For DK entries, copy `Entry ID, Contest Name, Contest ID, Entry Fee` verbatim and write the ID (or `Name (ID)`) in each slot. A bare name is rejected per instruction 4.
3. Write exactly one lineup per entry row the user registered. Never exceed `[N Entry Max]` or the file's row count.
4. In the last hour, keep players whose games have locked (DK marks them ` (LOCKED)`; derive lock time from `Game Info`), and emit a new file for re-upload.
5. Keep contest rules in a versioned local registry, checked against the file's header, slots, salaries and `Roster Position`. When unsure, ask the user to paste the rules text.
6. Report honestly: projected distributions and percentiles, with no promise of a top-2.3% finish.
7. Keep a spend and entry log, with optional user limits.

**MUST NOT:**
1. Log in to, automate, scrape or call DK or FD in any way, including `api.draftkings.com` draftables/rules JSON, browser automation or headless browsers.
2. Submit, enter, edit or late-swap automatically, or schedule anything that touches the site.
3. Run multiple accounts or get around experienced-player or beginner limits.
4. Redistribute DK or FD content beyond the user's machine.
5. Handle pick'em or prop products (DK Pick6 and similar): they are out of scope.

## 10. State legality and responsible gaming (all [UNVERIFIED]; verify for the user's state)

- **Legality.** DK and FD have historically offered no paid DFS in Hawaii, Idaho, Montana, Nevada and Washington, and Louisiana is parish-by-parish. In July 2025 the California AG issued a formal opinion that DFS is illegal sports betting under state law; later litigation and operator responses are unknown. [UNVERIFIED] Check the sites' "where is it legal" pages and the state regulator every season.
- **Age.** 18+ in most states, 19+ in Alabama and Nebraska, 21+ in some (e.g. Arizona, Iowa, Massachusetts). [UNVERIFIED]
- **Responsible gaming.** Both sites offer deposit limits, entry and spend limits, cool-offs and self-exclusion (DK Responsible Gaming Center, FD PlayWell). [UNVERIFIED] The helpline is **1-800-GAMBLER**. [UNVERIFIED] The tool shows cumulative spend and net result, supports stop-loss limits, and never markets guaranteed results.

---

## 11. Open questions (blocking or important)

1. **DK and FD Terms of Use, verbatim.** This blocks compliance sign-off.
2. **Sleeper live docs and general Terms of Use.** The mirror is verified. Is there a commercial-use restriction?
3. **The Odds API.** Free credits per month (500?), whether props are on the free plan, event-endpoint cost, and terms.
4. **CFBD site terms.** The quota of 1,000/month and the Patreon-only endpoints are now verified.
5. **Upstream-derived data policy.** Does the user accept nflverse depth charts (ESPN upstream) and sportsdataverse ESPN sets, given the Disney ToU automation clause? Default: no for ESPN-derived, yes for the NFL-official injury and roster fields.
   **Decided (2026-10-21, before merge; the owner deferred to the engineer):** accepted for personal, non-commercial use with no redistribution. The engine downloads only the public GitHub release files and never contacts ESPN itself. ESPN's own data APIs stay blocked in `dfs/sources.py`. College slates can run without these sets via `--provider generic`. Revisit before any commercial use or sharing of outputs built from them.
6. **nflverse schedules/lines licence.** `nfldata` has no licence and the line provenance is undocumented. Is personal, no-redistribution use acceptable, or must lines come from The Odds API?
7. NHL, MLB and NBA terms for personal automated use.
8. **A free ALLOWED source for NFL game-day inactives.** None was found. Is web search inside the 60-minute budget enough?
9. **Per-player routes for 2026.** None are free; participation arrives only after the postseason. Accept snaps, targets and NGS as the proxy?
10. CFB availability reports in 2026: do they exist, and where?
11. The user's state, for legality and minimum age.
12. Whether the Anthropic org has web search enabled and a Console domain allowlist set (disabled search returns a 400), and which model tier runs search (dynamic filtering needs Claude 4.6+).
