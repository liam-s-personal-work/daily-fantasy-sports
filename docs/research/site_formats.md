# DraftKings and FanDuel: contest file formats, roster rules, and a rules registry (fact-checked)

Research date: 2026-10-21. Topic key: `site_formats`. This is the adversarially fact-checked version of `site_formats.md`.
It is self-contained and replaces the original.

Tag legend used inline:
- `[VERIFIED: url]`: I re-checked it myself today against the cited artifact (downloaded raw and parsed with Python, or read in source code).
- `[CORRECTED: was X; url]`: the original said X; the evidence says otherwise, and the corrected text is what you see.
- `[UNVERIFIED]`: I could not re-check it today (the page is blocked, or the claim rests only on a search snippet the original agent saw). Treat it as a hint, not a fact.

"VERIFIED" against a GitHub-hosted file means a **site-generated CSV that a third party committed to GitHub**, or a third party's code. Those copies are
not served by DraftKings (DK) or FanDuel (FD). Where a fact rests only on third-party code (not a site file), the text says "(third-party code)".

---

## 0. What was checked, what was blocked, and what changed

### 0.1 Access today

- Every official DK/FD host is unreachable from this environment. WebFetch returns `getaddrinfo ENOTFOUND` for `www.draftkings.com` and
  `www.fanduel.com` (including `https://www.fanduel.com/robots.txt`). `curl` through the agent proxy returns `CONNECT tunnel failed, response 403` for
  `www.draftkings.com/robots.txt`, `www.fanduel.com/robots.txt`, `api.draftkings.com`, `help.draftkings.com`, `support.fanduel.com` and `web.archive.org`.
  WebFetch refuses `web.archive.org`. [VERIFIED: this session's WebFetch and curl errors]
- `www.thefantasyfootballers.com`, `www.stokastic.com`, `www.pff.com`, `establishtherun.com` and `www.legalsportsreport.com` are also unreachable
  (WebFetch ENOTFOUND; curl gets no connection). [VERIFIED: this session]
- **The WebSearch budget for this turn (200 calls, shared across agents) was already used up** when this check started. The tool's reply said:
  "this turn's web search budget is used up (limit: 200 WebSearch calls per turn, shared by every agent in it)". [VERIFIED: this session's WebSearch response]
- What remained reachable: `raw.githubusercontent.com`, GitHub code search (GitHub MCP tool), `github.com` and `pypi.org`. So every format fact below that
  is VERIFIED was re-derived from raw site-generated CSV files or source code on GitHub. Every claim that rested only on a search snippet of a news or official
  page (the original's W1–W20) is now `[UNVERIFIED]`, unless an independent file or code confirms it.
- **No official rules page, Terms of Service or robots.txt was read by either agent.** [VERIFIED: this session]

### 0.2 Corrections to the original (summary; details inline)

1. The DST trailing-space quirk (`"Titans  (44221417)"`) occurs in the **entries-file pool** (Sep 2026), **not** in the Oct 2026 standalone
   `DKSalaries` export, where `Name` is `Rams` with no trailing space.
2. The example `BUF@MIA 10/26/2026 01:00PM ET` appears in **no source** (and 2026-10-26 is a Monday), so it was removed.
3. The `Status` column and the UTF-8 BOM are **not** in every 2026 DK export. A Jan 2026 DK NBA Showdown export has 9 columns, no `Status` and no BOM.
   Both appear in the Sep–Oct 2026 standalone exports.
4. FD's "two empty-named columns" (index 14 and 15) are **named `Probable Pitcher` and `Batting Order` in FD MLB files** (May 2026). They are blank-named in
   other sports.
5. DK CFB's superflex token is **`S-FLEX`** in `Roster Position` (Oct 2022). FD CFB's is **`Super FLEX`**. The original guessed `SUPERFLEX`/`SUPER FLEX`.
6. FD CFB does **not** list tight ends as WR. FD keeps `Position = TE` but gives TEs `Roster Position = WR/Super FLEX`. DK CFB has no `TE` position at all.
7. The full `INVALID_TEAM_COUNT_SD` message ends with "Please fix before continuing.". The messages for `INVALID_PLAYER_COUNT` and `DUPLICATE_PLAYER` were added.
8. DK's glossary status code for Out is `O`, but its display text is `OUT`, and the CSV `Status` column uses `OUT`. Postponed is shown as `PPD` and
   Game-Time Decision as `GTD`. PUP means "Physically Unable to Perform".
9. `In Progress` and `Final` in `Game Info` are now confirmed in a **real Oct 20 2026 DK entries file**, not only by the pydfs code.
10. The S2 late-swap writer (JonnyRank) writes locked cells back **with the ` (LOCKED)` tag included**, and writes every entry, changed or not. The original
    suggested dropping the tag.
11. FD Single Game's `Roster Position` text `MVP - 1.5X Points/AnyFLEX` already existed in **2021 legacy** files (17 columns, no `MVP 1.5x Salary`).
    The text alone cannot tell the current format from the legacy one; only the `MVP 1.5x Salary` column can.
12. `[3 Entry Max]` appears in no source checked. `[150 Max Entry]` appears only in a synthetic fixture (S16).
13. Tool vintages: pydfs-lineup-optimizer 3.6.1 was released 2021-09-27. draftfast's latest PyPI release is 3.12.5 (2024-01-29). The original said "about 2021–2023".
14. pydfs registers its DK college-football settings under `Sport.CANADIAN_FOOTBALL` (a bug), so pydfs cannot be relied on for CFB.
15. For legacy FD MLB single game, pydfs uses a $60,000 budget (inherited), while draftfast uses $35,000. The sources conflict.
16. FD late-swap and entries files: one third-party repo describes a second, "current" FD template layout with a slot-only header (no entry_id columns).
    It is noted as an UNVERIFIED alternative that the parser must also accept.
17. Omission: DK MLB pitchers have `Position` `SP`/`RP` but `Roster Position` **`P`**, and DK MLB has no UTIL. Eligibility must come from `Roster Position`.
18. Omission: FD player lists from 2020 (NHL) have **16 columns and no `Roster Position`** column, so it must be optional.
19. Omission: DK CS:GO names can carry a **leading space** (`" iM"`), and team abbreviations can be mixed case (`Lil`).
20. Omission: DK offers CFL (in season on 2026-10-21) per old pydfs settings: QB, RB, WR, WR, FLEX, FLEX, DST. Low confidence, and not in the registry.

---

## Source index

Original sources, re-checked (S) and new sources found during the check (N). W = web snippets the original agent saw; none could be re-fetched today.

| ID | Source | What it is | Status today |
|---|---|---|---|
| S1 | https://raw.githubusercontent.com/troyseanwilliams-hash/Fantasy-Lineup-Optimizer/main/attached_assets/DKEntries_(17)_1773687635103.csv | DK NBA entries export, 16 Mar 2026, 150 entries, "NBA $25K mini-MAX [150 Entry Max]" | downloaded, parsed |
| S2 | https://github.com/JonnyRank/nfl-dfs-optimizer/tree/HEAD/tests/fixtures/public (`DKEntriesClassic.csv`, `DKEntriesClassicLateSwap.csv`, `DKEntriesShowdown.csv`), `src/nfl_dfs_optimizer/entries.py`, `README.md` | DK NFL entries files, Sep 2026. The late-swap file looks **hand-edited** (contest names "NFL Parity Contest 195905999") | downloaded, parsed |
| S3 | https://github.com/drgregmscott-tech/DFS_Optimizer/tree/HEAD/data/raw_salaries (4 files) and `scripts/ingest_salaries.py` | DK/FD NFL salary exports, Sep–Oct 2026, plus the author's notes from a live roster builder | downloaded, parsed |
| S4 | https://raw.githubusercontent.com/travismangone/matchup-terminal/HEAD/data/dk_salaries.csv | DK PGA export, "Biltmore Championship Asheville" (2026) | downloaded, parsed |
| S5 | https://github.com/jackdegen/nba-props/tree/HEAD/data (`current-fanduel.csv`, `current-fanduel-sg.csv`) | FD NBA classic (fixture 125663) and single-game (125658, CLE@PHI) lists, Jan 2026 | downloaded, parsed |
| S6 | https://raw.githubusercontent.com/umrebel3092/Umreble3092/HEAD/FanDuel-NFL-2025-CDT-10-CDT-19-CDT-121692-players-list.csv | FD NFL single game, ATL@SF, 19 Oct 2025 | downloaded, parsed |
| S7 | https://github.com/dschrengost/projections-v2 (`docs/pipeline/FANDUEL_INTEGRATION_SPEC.md` v0.5 2026-03-15; `tests/api/test_entry_manager_fd_csv.py`; `projections/api/entry_manager_api.py`) | FD entries-template contract. The spec names a live template file `docs/pipeline/FanDuel-NBA-2026-03-15-127613-entries-upload-template.csv`, but it is **not committed** (raw URL returns 404). The test fixture is synthetic ("FD Contest", "1) Fill slots") | downloaded, read |
| S8 | https://github.com/fadcrush/dfs-optimizer-engine (`frontend/src/lib/late-swap/import/importFanDuelEntries.ts`, `__tests__/fdPipeline.test.ts`) | FD entries-template parser (third-party code). Its test CSVs are synthetic (IDs `10001001`, `50000001`) | downloaded, read |
| S9 | https://github.com/gacolitti/draft.kings/tree/HEAD/tests/testthat (`optimize/url/lineups/{1,70,81,96}/rules.json`, `contest/url/lineups/159/rules.json`) | Captured DK gametype-rules API responses (package v0.5.5; capture date unknown) | downloaded, parsed |
| S10 | https://raw.githubusercontent.com/SeanDrum/Draft-Kings-API-Documentation/HEAD/readme.md | Unofficial DK API docs | downloaded, read |
| S11 | https://raw.githubusercontent.com/jaebradley/draftkings_client/HEAD/draft_kings/url_builder.py ; https://raw.githubusercontent.com/kyle1/draftkings-api/HEAD/draftkings-api/game_type.py | DK API URL patterns and the rules-object model | downloaded, read |
| S12 | https://raw.githubusercontent.com/scott-mccartney/dk-lineup-generator/HEAD/templates/nhl.csv | DK blank lineup-upload template, NHL, games dated 11/27/2024 | downloaded, parsed |
| S13 | pydfs-lineup-optimizer 3.6.1 (released 2021-09-27 per https://pypi.org/pypi/pydfs-lineup-optimizer/json); code at https://raw.githubusercontent.com/DimaKudosh/pydfs-lineup-optimizer/master/pydfs_lineup_optimizer/ (`sites/*/settings.py`, `rules.py`, `lineup_exporter.py`) | Old rule encodings (third-party code) | downloaded, read |
| S14 | https://github.com/BenBrostoff/draftfast (`draftfast/rules.py`, `constants/*.py`, `csv_parse/uploaders.py`); latest PyPI 3.12.5, 2024-01-29 (https://pypi.org/pypi/draftfast/json) | Old rule encodings (third-party code); quotes an FD support article | downloaded, read |
| S15 | https://raw.githubusercontent.com/shawtes/mlb-draftkings-system/HEAD/docs/archive/DRAFTKINGS_FORMAT_FIX_SUMMARY.md | That author's description of a DK MLB entries header | downloaded, read |
| S16 | https://raw.githubusercontent.com/SamBamCH/ivan/HEAD/tests/mma/data/entries.csv | **Synthetic** DK MMA entries fixture (Contest ID `900000001`; non-standard instruction text) | downloaded, read |
| S17 | https://raw.githubusercontent.com/isaiaherb/fantasy-hockey-optimization/HEAD/data/DKSalaries11Feb2023.csv | DK NHL export, 11 Feb 2023 | downloaded, parsed |
| S18 | https://raw.githubusercontent.com/pureslurp/DKPGA/HEAD/2023/Albany/CSVs/DKSalaries-Albany.csv ; https://raw.githubusercontent.com/markjoeljimenez/optidfs.backend/HEAD/csv/dkcsgo.csv | DK PGA (Hero World Challenge) and CS:GO (Dec 2020) exports | downloaded, parsed |
| N1 | https://raw.githubusercontent.com/JonnyRank/nba-dfs-optimizer/HEAD/tests/fixtures/2026-10-20/Post-L0-DKEntries.csv ; https://raw.githubusercontent.com/JonnyRank/nba-dfs-optimizer/HEAD/tests/fixtures/2026-10-20/Post-L2-DKEntries.csv | DK NBA entries files, **20 Oct 2026**, 40 entries in 2 contests, mid-slate with `(LOCKED)` cells | new |
| N2 | https://raw.githubusercontent.com/NastyAnalytics/DFS/HEAD/CFB/DKSalaries.csv | DK CFB salary export, 15 Oct 2022 | new |
| N3 | https://raw.githubusercontent.com/NastyAnalytics/DFS/HEAD/CFB/FD_LU.csv | FD CFB player list, fixture 81856 (Oct 2022) | new |
| N4 | https://raw.githubusercontent.com/alturn84/BARREL-PROOF-FLASK/HEAD/FanDuel/FanDuel-MLB-2026%20EDT-05%20EDT-18%20EDT-130513-players-list.csv | FD MLB player list, 18 May 2026, fixture 130513 | new |
| N5 | https://raw.githubusercontent.com/anthonyshook/dfsOptimizer/HEAD/inst/DFS%20Slate%20Files/hockey/FanDuel-NHL-2020-01-13-42628-players-list.csv | FD NHL player list, 13 Jan 2020 | new |
| N6 | https://raw.githubusercontent.com/jackdegen/nba-props/HEAD/data/current-draftkings-sg.csv | DK NBA Showdown export, CLE@PHI 01/16/2026 | new |
| N7 | https://raw.githubusercontent.com/JonPGH/dailyMatchups/HEAD/dksalaries_early.csv | DK MLB export with 3 extra user-added columns (`SlateDate`, `SlateName`, `SlateStartTime`) | new |
| N8 | https://raw.githubusercontent.com/ssisk44/NFLDataScienceProject/HEAD/DFS/DFS_DATA/DFS_FILES/2021/Week2/09192021KCBAL_BEFORE_GAME_reduced_players.csv | FD NFL single game, KC@BAL, Sep 2021 (legacy; header renamed 0..16 by the owner) | new |
| N9 | https://raw.githubusercontent.com/meinhardtmr/NFL-Fantasy/HEAD/2025/Excel/VBA/NFL.bas | 2025 FD single-game builder (third-party code): 6 players, `mvpSalary`, $60,000, 1–5 per team | new |
| N10 | https://raw.githubusercontent.com/danadajian/shared_fantasy_constants/HEAD/src/constants.ts ; https://raw.githubusercontent.com/kkeeling/lottery/HEAD/django/lottery/nfl_sd/tasks.py ; https://raw.githubusercontent.com/n-roth12/DFSLineupOptimizer/HEAD/lineup_optimizer/settings/fanduel_settings.py | Third-party FD upload headers: `MVP - 1.5X Points,AnyFLEX,…` (legacy 5-man); FD NFL classic CSV_COLS | new |
| N11 | https://raw.githubusercontent.com/jjoseph203146/Wynners_Fantasy_Spot/HEAD/wfs_pages/build_lineups.py | Third-party FD template parser: two layouts; FD NFL legality: max 4 per team, $60,000 | new |
| N12 | https://raw.githubusercontent.com/Waltiv26/Fanduel/HEAD/fdfs/export.py | Third-party: "FanDuel's downloaded entries template (entry_id,contest_id,contest_name,entry_fee,<slots...>)" | new |
| W1–W20 | The original's search-snippet sources (FanDuel Research, x.com tweets, RotoWire, Stokastic, PFF, Fantasy Footballers, DK Network, etc.; full list in §4–§5 where used) | News and help pages | **not re-checkable today** (blocked + search budget exhausted) |

---

## 1. DraftKings player/salary export (`DKSalaries.csv`)

### 1.1 Exact header

Standalone export, Sep–Oct 2026:

```
Position,Name + ID,Name,ID,Roster Position,Salary,Game Info,TeamAbbrev,AvgPointsPerGame,Status
```

- 10 columns. [VERIFIED: S3 dk_classic_wk6_main_18Oct2026.csv and dk_showdown_wk6_DAL_GB_18Oct2026.csv; S4]
- **BOM:** the Oct 2026 S3 files and the 2026 S4 PGA file begin with `EF BB BF`. The Jan 2026 DK NBA Showdown export (N6), the 2022 CFB export (N2), the 2023 NHL
  export (S17) and **all entries files** (S1, S2, N1) have **no BOM**. Always decode with `utf-8-sig`.
  [CORRECTED: was "The file begins with a UTF-8 BOM"; https://raw.githubusercontent.com/jackdegen/nba-props/HEAD/data/current-draftkings-sg.csv]
- **`Status` is optional.** It is absent from 2020–2023 exports (S17, S18, N2), absent from the Jan 2026 NBA Showdown export (N6), absent from the pool embedded in
  entries files as late as **20 Oct 2026** (N1), and present in Sep–Oct 2026 standalone exports (S3, S4). It was added at some point between Jan and Sep 2026;
  the exact date is UNVERIFIED. Map columns by header name.
  [CORRECTED: was "Older exports (2020–2024) … have 9 columns"; it was also absent in Jan 2026; https://raw.githubusercontent.com/jackdegen/nba-props/HEAD/data/current-draftkings-sg.csv]
- Third-party files may carry user-added columns (N7 adds `SlateDate,SlateName,SlateStartTime`). Keep unknown columns as passthrough. [VERIFIED: N7]
- Line endings vary (CRLF in S1, S4, S17; LF in S2, S3). Use `csv.reader` with `newline=''`. [VERIFIED: S1, S2, S3, S4, S17]
- `Status` values seen in Oct 2026: `""` (407), `IR` (65), `Q` (26), `OUT` (13), `D` (2) in the classic main slate; `""`, `IR`, `OUT`, `Q` in Showdown.
  [VERIFIED: S3 dk_classic_wk6_main_18Oct2026.csv]
- DK rules-API glossary (S9), title / status code / display text: Questionable `Q`/`Q`; Out **`O`/`OUT`**; Doubtful `D`/`D`; Injured Reserve `IR`/`IR`;
  Physically Unable to Perform `PUP`/`PUP`; Postponed Game (no code)/**`PPD`** (NFL, gameTypes 1 and 96). NBA (gameType 70) has "In Starting Lineup"
  (icon only) and **Game-Time Decision `GTD`**, and no D/IR/PUP. The CSV `Status` column uses the display text (`OUT`), not the code `O`.
  [CORRECTED: was "O (Out) … 'Postponed Game'"; https://github.com/gacolitti/draft.kings/blob/HEAD/tests/testthat/optimize/url/lineups/1/rules.json]

### 1.2 Example rows (real, Oct 2026)

```
RB,Bijan Robinson (44475736),Bijan Robinson,44475736,RB/FLEX,8700,CHI@ATL 10/18/2026 01:00PM ET,ATL,25.4,
WR,CeeDee Lamb (44531550),CeeDee Lamb,44531550,CPT,16200,DAL@GB 10/18/2026 08:20PM ET,DAL,24.8,
WR,CeeDee Lamb (44531495),CeeDee Lamb,44531495,FLEX,10800,DAL@GB 10/18/2026 08:20PM ET,DAL,24.8,
DST,Rams (44476586),Rams,44476586,DST,3800,ARI@LAR 10/18/2026 04:05PM ET,LAR,4.4,
```
[VERIFIED: S3 dk_classic_wk6_main_18Oct2026.csv, dk_showdown_wk6_DAL_GB_18Oct2026.csv]

### 1.3 Field semantics

| Column | Meaning and format | Tag |
|---|---|---|
| `Position` | The **natural** position(s), joined with `/`. NFL `QB`,`RB`,`WR`,`TE`,`DST` (plus `K` in Showdown); NBA `PG`,`SG`,`SF`,`PF`,`C`,`PG/SG`,`SG/SF`,`SF/PF`,`PF/C`,`PG/SF`; NHL `C`,`LW`,`RW`,`D`,`G`; MLB `SP`,`RP`,`C`,`1B`,…, multi-eligible `2B/3B` etc.; CFB `QB`,`RB`,`WR` only; PGA `G`; CS:GO `P`. Showdown keeps the true position (CPT rows are still `WR`, etc.). | [VERIFIED: S1, S3, S17, N2, N7, S4, S18] |
| `Name + ID` | `"<Name> (<ID>)"`. **DST quirk, entries-file pool only:** in the Sep 2026 entries files, `Name` is `"Seahawks "` / `"Titans "` with a trailing space, so `Name + ID` has two spaces (`"Titans  (44221417)"`). In the Oct 2026 standalone export it is `Rams (44476586)` with one space and no trailing space. **CS:GO** names can have a **leading** space (`" iM (16010626)"`). Always `strip()` and match the ID with a regex. | [CORRECTED: was a universal DST quirk; https://github.com/drgregmscott-tech/DFS_Optimizer/blob/HEAD/data/raw_salaries/dk_classic_wk6_main_18Oct2026.csv vs https://github.com/JonnyRank/nfl-dfs-optimizer/blob/HEAD/tests/fixtures/public/DKEntriesClassic.csv; CS:GO: S18 dkcsgo.csv] |
| `ID` | Numeric DK draftable ID, **8 digits** in every file checked (2020 CS:GO, 2022 CFB, 2023 NHL, 2026 NFL/NBA/PGA). It is **specific to a draft group**: Bijan Robinson is `44475736` (Oct 18 2026 classic), `44225918`/`44225865` (Sep 24 2026 Showdown CPT/FLEX) and `24717219` (Oct 2022 CFB). In Showdown, the CPT and FLEX rows of one player have different IDs. | [VERIFIED: S2 DKEntriesShowdown.csv, S3, N2, S17, S18] |
| `Roster Position` | **The authoritative eligibility list**, `/`-joined slot tokens. NFL classic: `QB`, `RB/FLEX`, `WR/FLEX`, `TE/FLEX`, `DST`. NBA classic: `PG/G/UTIL`, `SG/G/UTIL`, `SF/F/UTIL`, `PF/F/UTIL`, `C/UTIL`, `PG/SG/G/UTIL`, `SG/SF/F/G/UTIL`, `SF/PF/F/UTIL`, `PF/C/F/UTIL`, `PG/SF/F/G/UTIL`. NHL: `C/UTIL`, `W/UTIL` (LW and RW both), `D/UTIL`, `G`. **MLB: pitchers `P` (Position SP/RP); hitters `C`, `1B`, `2B/3B`, `OF`, … with no UTIL token.** **CFB: `QB/S-FLEX`, `RB/FLEX/S-FLEX`, `WR/FLEX/S-FLEX`.** Showdown: `CPT` plus `FLEX` (NFL) or `UTIL` (NBA). PGA `G`. | [VERIFIED: S1, S3, S17, N6; MLB: N7; CFB: https://raw.githubusercontent.com/NastyAnalytics/DFS/HEAD/CFB/DKSalaries.csv] |
| `Salary` | Integer dollars, no `$` or comma. Showdown CPT salary is **exactly 1.5x** its FLEX/UTIL twin on every pair checked: NFL 53/53 (Sep 2026), NFL 55/55 (Oct 2026), NBA 34/34 (Jan 2026), CS:GO 20/20 (2020). The minimum seen is $200 for an NFL Showdown FLEX and $1,000 for an NBA Showdown UTIL. | [VERIFIED: S2, S3, N6, S18] |
| `Game Info` | `"AWAY@HOME MM/DD/YYYY hh:mmAM ET"`: away team first, zero-padded 12-hour clock, no space before AM/PM, the literal ` ET`. Every row of S1, S2, S3, S17 and N1 (pre-lock) matches `^[A-Z]+@[A-Z]+ \d\d/\d\d/\d{4} \d\d:\d\d(AM\|PM) ET$`. Examples: `CHI@ATL 10/18/2026 01:00PM ET`, `ORL@ATL 03/16/2026 07:00PM ET`, `OKC@SAS 10/20/2026 09:30PM ET`. Parse with `%m/%d/%Y %I:%M%p` in `America/New_York`. **Once a game starts, the cell becomes `In Progress` and then `Final`** (seen in a real 20 Oct 2026 entries file; pydfs also skips these two). **PGA:** the tournament name, with no `@` or time (`Biltmore Championship Asheville`, `Hero World Challenge`). **Esports:** team tags can be mixed case (`VPP@Lil 12/22/2020 06:00AM ET`), so do not require `[A-Z]`. "Postponed": UNVERIFIED as a cell value (only `PPD` in the glossary). | [VERIFIED: S1, S2, S3, S4, S18; In Progress/Final: https://raw.githubusercontent.com/JonnyRank/nba-dfs-optimizer/HEAD/tests/fixtures/2026-10-20/Post-L2-DKEntries.csv and S13 draftkings/classic/importer.py] [CORRECTED: removed example "BUF@MIA 10/26/2026 01:00PM ET", which is in no source] |
| `TeamAbbrev` | DK's abbreviation (`JAX`, `LAR`, `WAS`, `NOP`, `BKN`, `GSW`, `PHX`, `SAS`, `NYK`, `OKC`, `TB`). CFB uses e.g. `BAMA`, `UT`, `OU`. PGA uses the literal `Golf`. | [VERIFIED: S1, S2, S3, S4, N1, N2] |
| `AvgPointsPerGame` | Float (one or two decimals: `25.4`, `28.10`). It is `0` for no games: 228 of 513 rows in the Oct 2026 NFL main slate, and Kyrie Irving `0` at $3,000 on 03/16/2026. Showdown shows the **same, unmultiplied** value on CPT and FLEX rows (53/53, 55/55, 34/34). | [VERIFIED: S1, S3, S2, N6] |
| `Status` | See §1.1 (optional column). | [VERIFIED: S3, N1] |

### 1.4 Multi-position eligibility and Showdown rows

- Classic: **one row per player** (no duplicate Name+Team in the Oct 2026 NFL main slate); eligibility = `Roster Position.split('/')`. [VERIFIED: S3]
- Showdown/Captain: **two rows per player**, `CPT` (1.5x salary, own ID) and `FLEX` (NFL) / `UTIL` (NBA). NFL Showdown pools include `K` and `DST`.
  Counts: 53 CPT + 53 FLEX (ATL@GB, 09/24/2026); 55 + 55 (DAL@GB, 10/18/2026); 34 + 34 NBA (CLE@PHI, 01/16/2026). [VERIFIED: S2, S3, N6]
- CS:GO (2020) uses the same CPT/FLEX two-row model with `Position = P`. [VERIFIED: S18 dkcsgo.csv]

---

## 2. DraftKings entries file ("Edit Entries" → download CSV, `DKEntries.csv`)

### 2.1 Layout

Four real exports agree (NBA 16 Mar 2026 [S1], NFL Sep 2026 [S2], NBA 20 Oct 2026 [N1]): an entries block on the left and an embedded player pool on the
right, **sharing rows**.

```
row 1 : Entry ID,Contest Name,Contest ID,Entry Fee,<slot_1>,…,<slot_n>,,Instructions[,,,…]
row 2 : <entry>,…,<slot cells>,,1. Column A lists all of your contest entries for this draftgroup
row 3 : <entry>,…,,2. Your current lineup is listed next to each entry (blank for reservations)
row 4 : <entry>,…,,3. You can change the lineup for an entry by entering the IDs of the players in the row next to that entry
row 5 : <entry>,…,,4. Use data from the Name+ID column or the ID column; you cannot use just the player's name
row 6 : <entry>,…,,5. For faster processing only include entries you are changing in the file you upload
row 7 : <entry>,…,,                     (empty in the Instructions column)
row 8 : <entry>,…,,Position,Name + ID,Name,ID,Roster Position,Salary,Game Info,TeamAbbrev,AvgPointsPerGame
row 9+: <entry or blanks>,…,,<player row, same 9 columns>
```
[VERIFIED: S1, S2 DKEntriesClassic.csv/DKEntriesShowdown.csv, N1 (all five instruction lines verbatim; pool header at row index 7)]

- Metadata columns, in order: **A=`Entry ID`, B=`Contest Name`, C=`Contest ID`, D=`Entry Fee`**. [VERIFIED: S1, S2, N1]
- Slot columns start at **index 4** and run to the first blank header cell. Labels repeat. Seen: NFL classic `QB,RB,RB,WR,WR,WR,TE,FLEX,DST`; NFL Showdown
  `CPT,FLEX,FLEX,FLEX,FLEX,FLEX`; NBA classic `PG,SG,SF,PF,C,G,F,UTIL`. [VERIFIED: S1, S2, N1] MLB `P,P,C,1B,2B,3B,SS,OF,OF,OF`: [VERIFIED: S15, that author's description of a
  real file; no raw MLB entries file found]. MMA `F,F,F,F,F,F`: only in a synthetic fixture [VERIFIED: S16 is synthetic].
- One blank separator column, then `Instructions` at index `4 + n_slots + 1` (NBA 13, NFL classic 14, Showdown 11). The pool header is on **row 8 (index 7)**,
  starting in the `Instructions` column. [VERIFIED: S1, S2, N1]
- Row widths are inconsistent: the NBA header has 14 cells and pool rows 22 (S1, N1), while the NFL S2 files pad every row to 23 (Showdown 20). **Never rely on widths.**
  [VERIFIED: S1, S2, N1]
- A user-edited DK entries file (https://raw.githubusercontent.com/firstprinciples/dfs_optimizer/HEAD/fps_dfs_optimizer/data/raw/test_DKEntries5.csv) has no
  `Instructions` cell. **Do not require `Instructions`** to classify the file. [VERIFIED: that file's header]
- The pool block in entries files has **9 columns (no `Status`)** in Mar, Sep and 20 Oct 2026 files. Map by name anyway. [VERIFIED: S1, S2, N1]
- Entry rows: `Entry ID` is 10 digits (`5090212743`, `5268167451`, `5303852548`); `Contest ID` is 9 digits (`188895738`, `195943226`, `196456024`). `Entry Fee` is a
  string with `$`: `$0.50`, `$4`, `$1`, and in the S2 NFL files with a **trailing space** (`$1 `, `$5 `, `$6 `). The real N1 file has no trailing space, so treat
  the space as possible, not guaranteed. [VERIFIED: S1, S2, N1]
- Contest names carry entry-limit tags: `[150 Entry Max]` (S1), `[20 Entry Max]` (S2, N1) and `[Single Entry]` (S2). Showdown names carry the matchup:
  `NFL Showdown $100K Huddle [Single Entry] (ATL @ GB)`. [VERIFIED: S1, S2, N1] `[3 Entry Max]`: [UNVERIFIED] (in no checked file).
  `[150 Max Entry]` appears only in synthetic S16.
- **One file covers all of the user's entries for one draft group, across several contests**: two Showdown contests (Huddle 195943226, Dimeback 195943255) in S2,
  and two NBA contests (196456024 "NBA $100K Four Point Play", 196456031 "NBA $30K And-One", 20 entries each) in N1. [VERIFIED: S2 DKEntriesShowdown.csv, N1]
- Slot cell = `"Name (ID)"` (`Josh Giddey (42297980)`), blank for a reservation, or **`"Name (ID) (LOCKED)"`** once that player's game has started. The real
  20 Oct 2026 NBA file shows 170 locked cells after the first game started and 320 later, e.g. `Payton Pritchard (44451268) (LOCKED)`.
  [VERIFIED: https://raw.githubusercontent.com/JonnyRank/nba-dfs-optimizer/HEAD/tests/fixtures/2026-10-20/Post-L0-DKEntries.csv ; S2 DKEntriesClassicLateSwap.csv]
- Entry rows run past the pool header. A row is an entry **only if column A is all digits**. After the last entry, A–D are blank. [VERIFIED: S1, S2 entries.py, N1]

### 2.2 Writing players on re-upload

- Instruction 4 verbatim: "Use data from the Name+ID column or the ID column; you cannot use just the player's name". So `ID` alone or `Name + ID` is accepted, and
  a bare name is rejected. [VERIFIED: S1, S2, N1, as DK's own instruction text inside the file]
- Instruction 5 verbatim: "For faster processing only include entries you are changing in the file you upload". [VERIFIED: S1, S2, N1]
- Writer: copy A–D verbatim (including any trailing space in the fee), then one cell per slot in header order. Write the `ID` or the exact `Name + ID` from the pool.
- **Locked slots:** the S2 late-swap writer writes "every entry (changed or not) … Each cell is DraftKings' own `Name + ID` text, `(LOCKED)` tag included, ready to
  upload", and keeps locked players in their slots. DK's own acceptance of the `(LOCKED)` suffix on upload is not documented in any primary source.
  **Recommendation: write locked cells back byte-for-byte as downloaded, and never move a locked player.**
  [CORRECTED: was "drop the ` (LOCKED)` suffix, or keep the ID"; https://github.com/JonnyRank/nfl-dfs-optimizer/blob/HEAD/README.md] (official behaviour [UNVERIFIED])
- Showdown: the CPT column needs the **CPT-row ID**, and the FLEX/UTIL columns need FLEX/UTIL-row IDs. [VERIFIED: S2 README "Showdown players appear twice … with
  **different IDs**"] The DK error for a misplaced player is `INVALID_POSITION`, "Lineup contains invalid roster positions or players are not in the correct position."
  [VERIFIED: S9 rules.json] (which error DK returns for a FLEX ID in CPT: [UNVERIFIED])

### 2.3 Blank lineup-upload template (new lineups, not tied to entries)

Real DK template, NHL, games dated 11/27/2024 [S12]:

```
row 1 : C,C,W,W,W,D,D,G,UTIL,,Instructions
row 2 : ,,,,,,,,,,1. Locate the player you want to select in the list below␠
row 3 : ,,,,,,,,,,2. Copy the ID of your player (you can use the Name + ID column or the ID column)␠
row 4 : ,,,,,,,,,,3. Paste the ID into the roster position desired␠
row 5 : ,,,,,,,,,,4. You must include an ID for each player; you cannot use just the player's name␠
row 6 : ,,,,,,,,,,5. You can create up to 500 lineups per file␠
row 7 : ␠                      (a single space)
row 8 : ,,,,,,,,,,Position,Name + ID,Name,ID,Roster Position,Salary,Game Info,TeamAbbrev,AvgPointsPerGame
```
(␠ = a trailing space present in the file) [VERIFIED: https://raw.githubusercontent.com/scott-mccartney/dk-lineup-generator/HEAD/templates/nhl.csv]

- There are no Entry ID or Contest columns; row 1 starts with the slot labels. The limit is **500 lineups per file** (instruction 5, as of Nov 2024; 2026 [UNVERIFIED]). [VERIFIED: S12]
- Upload page `https://www.draftkings.com/lineup/upload`, as cited in draftfast's error message "Check that you're using the DK CSV upload template, … https://www.draftkings.com/lineup/upload."
  [VERIFIED: https://github.com/BenBrostoff/draftfast/blob/master/draftfast/csv_parse/uploaders.py] (that the page still exists: [UNVERIFIED])
- Workflow for real contest entries: register N entries, download the pre-filled entries CSV (§2.1), fill it, upload it. The README for S2 describes "Download your entries
  CSV from DraftKings' Edit Entries page" and re-upload "on the Edit Entries page". [VERIFIED: S2 README] (W19 snippets: [UNVERIFIED])

---

## 3. FanDuel player list and entries template

### 3.1 Player list ("Download players list") exact headers

Classic (NFL Oct 2026 [S3], NBA Jan 2026 [S5], CFB Oct 2022 [N3]):

```
Id,Position,First Name,Nickname,Last Name,FPPG,Played,Salary,Game,Team,Opponent,Injury Indicator,Injury Details,Tier,,,Roster Position
```
[VERIFIED: S3 fd_classic_wk6_main_18Oct2026.csv, S5 current-fanduel.csv, N3]

- 17 columns. **For non-MLB sports, index 14 and 15 have empty names** (pandas: `Unnamed: 14`/`Unnamed: 15`) and are always empty. No BOM. [VERIFIED: S3, S5, N3]
- **MLB names those two columns:** `…,Tier,Probable Pitcher,Batting Order,Roster Position`. `Probable Pitcher` is `Yes` or blank (18 `Yes` on a 9-game slate);
  `Batting Order` was blank on every row in that pre-lineup file.
  [CORRECTED: was "two empty-named columns … sport-specific MLB columns UNCONFIRMED"; https://raw.githubusercontent.com/alturn84/BARREL-PROOF-FLASK/HEAD/FanDuel/FanDuel-MLB-2026%20EDT-05%20EDT-18%20EDT-130513-players-list.csv]
- **Older FD lists lack `Roster Position`:** a Jan 2020 FD NHL list has 16 columns, ending `…,Tier,,`. `Roster Position` must be optional, with eligibility then
  derived from `Position` and the registry. [VERIFIED: https://raw.githubusercontent.com/anthonyshook/dfsOptimizer/HEAD/inst/DFS%20Slate%20Files/hockey/FanDuel-NHL-2020-01-13-42628-players-list.csv]
- **Single Game** inserts `MVP 1.5x Salary` after `Salary` (18 columns): NFL 19 Oct 2025 [S6], NFL Sep 2026 [S3], NBA Jan 2026 [S5]. [VERIFIED: S3, S5, S6]

```
Id,Position,First Name,Nickname,Last Name,FPPG,Played,Salary,MVP 1.5x Salary,Game,Team,Opponent,Injury Indicator,Injury Details,Tier,,,Roster Position
```

- **Legacy Single Game files (2021) have 17 columns, with no `MVP 1.5x Salary`, but already use `Roster Position = MVP - 1.5X Points/AnyFLEX`.** The Roster Position text
  therefore does not distinguish the formats; the `MVP 1.5x Salary` column does.
  [CORRECTED: the original implied the text signals the current format; https://raw.githubusercontent.com/ssisk44/NFLDataScienceProject/HEAD/DFS/DFS_DATA/DFS_FILES/2021/Week2/09192021KCBAL_BEFORE_GAME_reduced_players.csv]

Example rows (real):

```
135326-62239,QB,Josh,Josh Allen,Allen,30.492001342773438,5,9500,BUF@LV,BUF,LV,,,,,,QB
135326-12555,D,Baltimore,Baltimore Ravens,Ravens,5,5,4900,BAL@CLE,BAL,CLE,,,,,,DEF
125663-14513,SF/PF,Kawhi,Kawhi Leonard,Leonard,47.41999918619792,30,9900,LAC@TOR,LAC,TOR,Q,Ankle,,,,SF/PF
133105-129458,WR,Jaxon,Jaxon Smith-Njigba,Smith-Njigba,18.539999389648436,20,13000,19500,NE@SEA,SEA,NE,,,,,,MVP - 1.5X Points/AnyFLEX
130513-52183,P,Tyler,Tyler Glasnow,Glasnow,37.714285714285715,7,11200,LAD@SD,LAD,SD,IL,Back,,,,P
```
[VERIFIED: S3, S5, N4]

| Column | Meaning and format | Tag |
|---|---|---|
| `Id` | `"<fixtureListId>-<playerId>"`. The prefix is identical on every row of a file (135326, 133105, 125663, 125658, 121692, 130513, 81856). The suffix is a **persistent FD player ID**: Bijan Robinson is `136855` in FD CFB 2022 (81856), NFL Oct 2025 (121692) and NFL Oct 2026 (135326), so it persists even from college to NFL. Tyrese Maxey is `145331` in 125663 and 125658. An `Id` is only valid in its own slate (inference). | [VERIFIED: S3, S5, S6, N3, N4] |
| `Position` | NFL `QB`,`RB`,`WR`,`TE`,`D`, plus `K` in Single Game. NBA `PG`,`SG`,`SF`,`PF`,`C`,`PG/SG`,`SG/PG`,`SF/PF`,`PF/SF`,`SF/SG`,`PF/C`,`C/PF`, … (order not normalised). MLB `P`,`C`,`1B`,`2B`,`3B`,`SS`,`OF`,`SS/2B`,`1B/OF`, …. NHL (2020) `C`,`W`,`D`,`G`. **CFB `QB`,`RB`,`WR`,`TE`.** | [VERIFIED: S3, S5, S6, N3, N4, N5] |
| `First Name` / `Nickname` / `Last Name` | `Nickname` is the full display name. Defense: `Baltimore` / `Baltimore Ravens` / `Ravens`. | [VERIFIED: S3, S6] |
| `FPPG` | A float with binary noise (`30.492001342773438`), **blank exactly when `Played` = 0** (88/88, 19/19, 14/14, 1/1). | [VERIFIED: S3, S5, S6] |
| `Played` | Games played (int). | [VERIFIED: S3] |
| `Salary` | Integer dollars. In Single Game it is the FLEX price. | [VERIFIED: S3, S6] |
| `MVP 1.5x Salary` | Single Game only; exactly `1.5 x Salary` on 58/58 (NFL Sep 2026), 57/57 (NFL Oct 2025) and 34/34 (NBA Jan 2026) rows. | [VERIFIED: S3, S6, S5] |
| `Game` | `"AWAY@HOME"`, **with no date or time** (`BUF@LV`, `NE@SEA`, `LAD@SD`, `MINN@ILL`), so the list gives no lock times. | [VERIFIED: S3, S5, N3, N4] |
| `Team`, `Opponent` | Abbreviations (CFB uses 2–4 letters: `CCU`, `MINN`). | [VERIFIED: S3, N3] |
| `Injury Indicator` | NFL `""`,`Q`,`O`,`D`,`IR`,`NA`; NBA `""`,`O`,`Q`,`P`,`D`; **MLB `""`,`IL`,`O`,`DTD`; NHL (2020) `""`,`IR`,`O`,`DTD`**; CFB `""`,`O`,`Q`,`P`,`D`. | [VERIFIED: S3, S5, N3, N4, N5] [CORRECTED: added MLB/NHL codes IL and DTD, which the original omitted] |
| `Injury Details` | Free text: `Ankle`, `Knee - acl`, `Knee - pcl`, `Undisclosed`, `Coach's decision`, … | [VERIFIED: S3, S6] |
| `Tier` | Empty in every 2022–2026 file checked; purpose [UNVERIFIED]. | [VERIFIED: S3, S5, S6, N3, N4] |
| col 14/15 | Unnamed and empty, except in MLB (`Probable Pitcher`, `Batting Order`). | [VERIFIED: S3, S5, N4] |
| `Roster Position` | Eligible slot tokens. NFL classic `QB`,`RB/FLEX`,`WR/FLEX`,`TE/FLEX`,`DEF`. NBA classic `PG`,`SG`,`SF`,`PF`,`C`,`PG/SG`,`SG/SF`,`SF/PF`,`PF/C` (normalised order). **MLB** `P`, `C/1B/UTIL`, `2B/UTIL`, `3B/UTIL`, `SS/UTIL`, `OF/UTIL`, `C/1B/OF/UTIL`, `2B/3B/SS/UTIL`, …. **CFB** `QB/Super FLEX`, `RB/Super FLEX`, `WR/Super FLEX` (TEs get `WR/Super FLEX`). Single Game `MVP - 1.5X Points/AnyFLEX` (NFL) and `MVP - 1.5X Points/UTIL` (NBA) on every row. | [VERIFIED: S3, S5, S6, N3, N4] |

### 3.2 FD entries template (edit existing entries; the "csv-edit" download)

- FD's CSV-edit page is said to offer "a CSV template of your current entries" and to match players by "Player ID + Player Name" or "Id" [UNVERIFIED: W7 snippet of
  https://www.fanduel.com/csv-edit; the page is blocked].
- Header contract, which S7 says was "Integrated from a live FanDuel entry-template CSV" on 2026-03-15 (the file itself is not committed):

```
entry_id,contest_id,contest_name,entry_fee,PG,PG,SG,SG,SF,SF,PF,PF,C,"",Instructions
```
  [VERIFIED as S7's stated contract: https://github.com/dschrengost/projections-v2/blob/HEAD/docs/pipeline/FANDUEL_INTEGRATION_SPEC.md and tests/api/test_entry_manager_fd_csv.py]
  A second independent repo describes the same prefix: "FanDuel's downloaded entries template (entry_id,contest_id,contest_name,entry_fee,<slots...>)" [VERIFIED: N12].
  Against an actual FD file this is [UNVERIFIED], so confidence is medium.
  - Metadata order differs from DK: `entry_id, contest_id, contest_name, entry_fee` (contest_id before contest_name), in lower snake_case.
  - S8's comment says `Entry ID,Contest ID,Contest Name,Entry Fee` (Title Case, same order), but its own pool comment uses snake_case, and its test CSVs are synthetic.
    **Normalise case, spaces and underscores.** [VERIFIED: S8 importFanDuelEntries.ts]
  - **Alternative layout [UNVERIFIED]:** one third-party parser (N11) treats the snake_case header as "legacy" and also accepts a "current lineup-upload layout" whose
    header is the slot labels only (`QB,RB,RB,WR,WR,WR,TE,FLEX,DEF`), with the embedded player table "farther right / farther down". The parser must accept both.
    [VERIFIED as that repo's claim: N11]
  - `contest_id` looks like `"127613-279208572"` (`<fixtureListId>-<contestId>`), `entry_id` is numeric (`3670015043`), and `entry_fee` has no `$` (`0.25`).
    [VERIFIED as S7 test fixture values; the fixture is partly synthetic, so FD's real values are UNVERIFIED]
  - Slot cells hold the player `Id` (`127613-157808`). The pool's `Player ID + Player Name` cell is `Id:Name` (`127613-157808:Cade Cunningham`), and S7 strips it to the
    `Id` before export (regex `^\s*(\d+-\d+)\s*:`). Whether FD accepts `Id:Name` inside a slot cell is [UNVERIFIED]; **write the bare `Id`.** [VERIFIED: S7 entry_manager_api.py `_FD_ID_PREFIX_RE`, `_fd_upload_cell_value`]
  - Embedded pool: `Player ID + Player Name` at index 14 (the `Instructions` column), then `Id`, `Position`, `First Name`, `Nickname`, … "Pool data appears both
    embedded in entry rows (cols 13+) AND in standalone empty-entry rows at the bottom of the file — same structure as DK." [VERIFIED: S8 comment; S7 fixture puts
    `Player ID + Player Name,Id,Position` at index 14] Whether the template's `Game` has a time is [UNVERIFIED] (S8's `ATL@BOS 07:30PM ET` is synthetic).
  - **No FD `(LOCKED)` marker** was found. S8 derives lock state from game time (`lockedByGameTime`), and S7 lists the "FD lock-time source" as an open question.
    [VERIFIED: S7 spec §10, S8]
- **FD new-lineup upload header** (slot labels then one row of `Id`s): NFL classic `QB,RB,RB,WR,WR,WR,TE,FLEX,DEF` [VERIFIED: N10 n-roth12 `CSV_COLS`; N11].
  Legacy NFL Single Game header `MVP - 1.5X Points,AnyFLEX,AnyFLEX,AnyFLEX,AnyFLEX` appears in three independent codebases (2021–2025) [VERIFIED: N10 danadajian
  constants.ts, kkeeling tasks.py; N9 line 706]. Legacy NBA single game `MVP - 2x Points,STAR - 1.5x Points,PRO - 1.2x Points,UTIL,UTIL` [VERIFIED: N10 danadajian].
  pydfs maps `MVP - 2X Points`, `STAR - 1.5X Points`, `PRO - 1.2X Points`, `Captain - 1.5x Pts` [VERIFIED: S13 lineup_exporter.py].
  **The current 6-slot header (presumably `MVP - 1.5X Points` + 5 x `AnyFLEX`/`UTIL`) is [UNVERIFIED].** Always copy labels from the downloaded template.

---

## 4. Roster rules by site and game type

Legend: cap = salary cap; games = minimum distinct games; teams = minimum distinct teams; max/team = maximum players from one team.

### 4.1 DraftKings machine-readable rule objects (captured API responses, capture date unknown) [S9]

| gameTypeId | gameTypeName | description (verbatim) | salaryCap | gameCount | teamCount | uniquePlayers | allowLateSwap | lineupTemplate | rulesUrl | lineupConfigurationId |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Classic | "Create a 9-player lineup while staying under the $50,000 salary cap" | enabled 0–50000 | enabled, min 2 | enabled, min 2 | true | true | QB,RB,RB,WR,WR,WR,TE,FLEX,DST | /help/rules/1/1 | 1 |
| 96 | Showdown Captain Mode | "Create your team from 1 game, while staying under the $50,000 salary cap" | 0–50000 | disabled | min 2 | true | false | CPT (positionTip "Scores 1.5x Fantasy Points", subtext "1.5x"), FLEX x5 | /help/rules/1/96 | 81 |
| 70 | Classic | "Create an 8-player lineup while staying under the $50,000 salary cap. Includes Late Swap." | 0–50000 | min 2 | min 2 | true | true | PG,SG,SF,PF,C,G,F,UTIL | /help/rules/4/70 | 55 |
| 81 | Showdown Captain Mode | "Create your team from 1 game, while staying under the $50,000 salary cap" | 0–50000 | disabled | min 2 | true | false | CPT ("Scores 1.5x Fantasy Points"), UTIL x5 | /help/rules/4/81 | 66 |
| 159 | Madden Showdown Captain Mode | same text as 96 | 0–50000 | disabled | min 2 | true | false | CPT, FLEX x5 | /help/rules/1/159 | 81 |

[VERIFIED: https://github.com/gacolitti/draft.kings/tree/HEAD/tests/testthat/optimize/url/lineups (1, 70, 81, 96) and .../contest/url/lineups/159/rules.json].
`teamPositionLimits` is `null` in all five. The CPT salary multiplier is **not** in the rule object (only the points tip). [VERIFIED: S9]

- Rule-object keys (DK's data model): `gameTypeId, gameTypeName, gameTypeDescription, lineupConfigurationId, salaryCap{isEnabled,minValue,maxValue}, gameCount{…},
  teamCount{…}, uniquePlayers, allowLateSwap, lineupTemplate[{rosterSlot{id,name,description,positionTip,positionTipSubtext,notScoring},order,scoringOrder}],
  errorStatus, errorCodes[{errorCode,defaultMessage}], rulesUrl, draftType ("SalaryCap"), allowedCompetitionAttributes, scoringDivider, useOptimalLineups,
  supportsPlayersTab, showByeWeekInfo, glossaryTerms, originalDraftType, isSeasonLong, teamPositionLimits`. [VERIFIED: S9, S11 game_type.py]
- Error codes and `defaultMessage`, verbatim:
  - Classic (1, 70): `EXCEEDS_SALARY` "Lineup exceeds salary limit."; `INVALID_TEAM_COUNT` "Lineups for this game style must include players from at least two different teams. Please fix before continuing.";
    `MULTIPLE_GAMES` "Lineup needs to contain players spanning multiple games."; `INVALID_PLAYER_COUNT` "Lineup does not meet the required number of players.";
    `DUPLICATE_PLAYER` "Lineup includes a player that is used more than once."; `INVALID_POSITION` "Lineup contains invalid roster positions or players are not in the correct position."
  - Showdown (81, 96, 159): `EXCEEDS_SALARY`, `INVALID_PLAYER_COUNT`, `DUPLICATE_PLAYER`, `INVALID_POSITION` (same texts), plus `INVALID_TEAM_COUNT_SD` "Lineups for this game style must include players from both teams. Please fix before continuing."
    There is no `MULTIPLE_GAMES` or `INVALID_TEAM_COUNT` in Showdown.
  [CORRECTED: was the INVALID_TEAM_COUNT_SD text without "Please fix before continuing.", with no messages for INVALID_PLAYER_COUNT/DUPLICATE_PLAYER; https://github.com/gacolitti/draft.kings/blob/HEAD/tests/testthat/optimize/url/lineups/96/rules.json]
- Rules page URL pattern `https://www.draftkings.com/help/rules/{sportId}/{gameTypeId}`. S11's comment reads "Game type IDs can be found at https://www.draftkings.com/help/rules/
  by clicking on a game type and viewing the second ID in the URL". sportId 1 = NFL and 4 = NBA, inferred from `rulesUrl`. [VERIFIED: S9, S11]
- Unofficial endpoints: `https://api.draftkings.com/lineups/v1/gametypes/{id}/rules`, `https://api.draftkings.com/draftgroups/v1/draftgroups/{id}/draftables`,
  `https://www.draftkings.com/lobby/getcontests?sport=X`, `https://api.draftkings.com/contests/v1/contests/{id}?format=json`,
  `https://api.draftkings.com/rules-and-scoring/RulesAndScoring.json`, `https://api.draftkings.com/sites/US-DK/sports/v1/sports?format=json`. [VERIFIED: S10, S11 url_builder.py]
  S10 says verbatim: "Draft Kings very much does NOT intend to have this API used by the public, but then again they don't lock the API down either." [VERIFIED: S10 line 3]
  **The engine must not call these** (see §6).

### 4.2 DraftKings by sport

| Sport / type | Slots (header labels) | Eligibility | Cap | Structural rules | Multipliers | Evidence and tag |
|---|---|---|---|---|---|---|
| **NFL Classic** | QB,RB,RB,WR,WR,WR,TE,FLEX,DST (9) | FLEX = RB/WR/TE | $50,000 | ≥2 games, ≥2 teams (so ≤8 from one team, implied); late swap on | none | [VERIFIED: S2 header, S3 tokens, S9 gameType 1; pydfs min_games 2 (S13)]. W12 snippet [UNVERIFIED] |
| **NFL Showdown (Captain Mode)** | CPT,FLEX,FLEX,FLEX,FLEX,FLEX (6) | any, incl. K and DST | $50,000 | one game; ≥1 player from each team ("must include players from both teams"); no late swap | CPT 1.5x points; CPT 1.5x salary (already in the CPT row's Salary) | [VERIFIED: S2, S3 (1.5x salary 53/53, 55/55), S9 gameType 96; pydfs captain `max_from_one_team = 5` (S13)] |
| **CFB Classic** | QB,RB,RB,WR,WR,WR,FLEX,**S-FLEX** (8; header label [UNVERIFIED], token verified) | FLEX = RB/WR (QBs carry only `QB/S-FLEX`); S-FLEX = QB/RB/WR; **no TE position at all** (TEs presumably filed as WR); no DST or K | $50,000 [UNVERIFIED] | ≥2 games (pydfs) | none | [CORRECTED: was SUPERFLEX/SUPER FLEX; Roster Position tokens `QB/S-FLEX`, `RB/FLEX/S-FLEX`, `WR/FLEX/S-FLEX` in https://raw.githubusercontent.com/NastyAnalytics/DFS/HEAD/CFB/DKSalaries.csv (Oct 2022)]. pydfs `SUPER FLEX` and min_games 2 [VERIFIED: S13], but registered as Sport.CANADIAN_FOOTBALL (bug). Slot count and cap from W9 snippets [UNVERIFIED] |
| **CFB Showdown** | CPT + 5 FLEX (6) | mostly QB/RB/WR; no team defense | $50,000 | ≥1 player from each team | CPT 1.5x points and salary | [UNVERIFIED: W10 snippet only] |
| **NBA Classic** | PG,SG,SF,PF,C,G,F,UTIL (8) | G = PG/SG; F = SF/PF; UTIL = any | $50,000 | ≥2 games, ≥2 teams; late swap on | none | [VERIFIED: S1, N1 headers and tokens; S9 gameType 70] |
| **NBA Showdown** | CPT,UTIL,UTIL,UTIL,UTIL,UTIL (6) | any | $50,000 | one game; both teams; no late swap | CPT 1.5x points; **CPT 1.5x salary (34/34 pairs, Jan 2026)** | [VERIFIED: S9 gameType 81; salary: https://raw.githubusercontent.com/jackdegen/nba-props/HEAD/data/current-draftkings-sg.csv] |
| **WNBA Classic** | G,G,F,F,F,UTIL (6) | G = PG/SG, F = SF/PF, UTIL any | $50,000 | ≥2 games | none | [VERIFIED as pydfs/draftfast encodings only: S13 DraftKingsWNBASettings, S14 salary_cap.py]; current rules [UNVERIFIED]. WNBA is in its offseason on 2026-10-21 |
| **MLB Classic** | P,P,C,1B,2B,3B,SS,OF,OF,OF (10) | P = Roster token `P` (Position SP/RP); hitters by token; **no UTIL** | $50,000 | **max 5 hitters from one team** (pitchers excluded); ≥2 games | none | [VERIFIED: S15 header; N7 tokens; S13 `DraftKingsBaseballRosterRule.MAXIMUM_HITTERS_FROM_ONE_TEAM = 5`, min_games 2; S14 max_players_per_team 7, hitters 5, min_matchups 2]. "changed from 6" (W14) [UNVERIFIED] |
| **MLB Showdown** | CPT + 5 UTIL (6) | any | $50,000 | one game | CPT 1.5x | [VERIFIED as code only: S13 captain-mode baseball (CPT + 5 UTIL, max 5 per team), S14 MLB_SHOWDOWN]; current [UNVERIFIED] |
| **NHL Classic** | C,C,W,W,W,D,D,G,UTIL (9) | W = LW/RW; UTIL = C/W/D (not G) | $50,000 | ≥3 teams, **counting skaters only** (pydfs `total_teams_exclude_positions = ['G']`); draftfast adds ≥2 games | none | [VERIFIED: S12 template header and tokens; S17 tokens; S13 min_teams 3; S14 min_matchups 2 / min_teams 3]. W13 snippet [UNVERIFIED] |
| **NHL Showdown** | CPT + 5 FLEX (pydfs) vs 6 FLEX (draftfast) | any | $50,000 | one game | CPT 1.5x (pydfs) | [VERIFIED as conflicting code: S13 captain-mode NHL; S14 `NHL_SHOWDOWN: [["FLEX", 6, 6]]`]; which is current: [UNVERIFIED] |
| **PGA Classic** | G x6 | `Position = G`, `Roster Position = G` | $50,000 | none | none | [VERIFIED: S4, S18 file format; S13, S14 for slots and cap] |
| **PGA Captain** | CPT + 5 G | — | $50,000 | — | CPT 1.5x [UNVERIFIED] | [VERIFIED as code only: S14 PGA_CAPTAIN] |
| **MMA** | F x6 | fighters | $50,000 | no late swap | none | [VERIFIED as code: S13 DraftKingsMMA; S14]; the late-swap rollback (W16) [UNVERIFIED] |
| **NASCAR** | D x6 | drivers | $50,000 | — | none | [VERIFIED as code: S13, S14] |
| **Soccer Classic** | GK,D,D,M,M,F,F,UTIL (8) | UTIL = D/M/F | $50,000 | ≥3 teams | none | [VERIFIED as code: S13 DraftKingsSoccerSettings]; draftfast ranges F 2–3, M 2–3, D 2–3, GK 1–2 (S14) |
| **Soccer Showdown** | CPT + 5 FLEX | any | $50,000 | one match | CPT 1.5x | [VERIFIED as code: S13 captain-mode soccer] |
| **Tennis Classic** | P x6 | players | $50,000 | — | none | [VERIFIED as code: S13, S14 `TEN`] |
| **CS2 (CS:GO)** | CPT + 5 FLEX, `Position = P` | any | $50,000 | max 3 from one team, ≥2 games | CPT 1.5x points; CPT salary 1.5x in the file (20/20) | [VERIFIED: S18 file (2020); S13 DraftKingsCSGOSettings; S14 max_players_per_team 3]; current rules [UNVERIFIED] |
| **LoL** | CPT,TOP,JNG,MID,ADC,SUP,TEAM (7) | by role | $50,000 | max 4 from one team; ≥2 games | CPT 1.5x | [VERIFIED as code: S13 DraftKingsLOLSettings]; whether offered today [UNVERIFIED] |
| **CFL** (omitted by the original) | QB,RB,WR,WR,FLEX,FLEX,DST (7) | FLEX = RB/WR | $50,000 | ≥2 teams | none | [VERIFIED as code only: S13 DraftKingsCanadianFootballSettings]; whether DK offers CFL in 2026 [UNVERIFIED] |
| **Tiers** (NFL/NBA/MLB/NHL) | one pick per tier, no cap | tier label from `Roster Position` | none | ≥2 games | none | [VERIFIED as code: S13 DraftKingsTiersSettings (budget None, min_games 2) and DraftKingsTiersRule (one per tier)]; still offered (W18) [UNVERIFIED] |

### 4.3 FanDuel by sport

| Sport / type | Slots | Eligibility | Cap | Structural rules | Multipliers | Evidence and tag |
|---|---|---|---|---|---|---|
| **NFL full roster** | QB,RB,RB,WR,WR,WR,TE,FLEX,DEF (9) | FLEX = RB/WR/TE; defense `Position D` → slot `DEF`; no kicker | $60,000 | max 4 from one team; ≥3 teams | none | [VERIFIED: S3 tokens (no K in pool); N10 CSV_COLS; S13 FanDuelSettings (budget 60000, max_from_one_team 4, min_teams 3); N11 legality checks "more than 4 … from one NFL team", "$60,000 cap"]. Official page [UNVERIFIED] |
| **NFL Single Game (current)** | MVP + 5 FLEX (6) | AnyFLEX = QB/RB/WR/TE/K/D | $60,000 | one game; ≥1 from each team, so ≤5 from one team | MVP 1.5x points AND 1.5x salary (`MVP 1.5x Salary`) | [VERIFIED: S3, S6 (column and positions, by Oct 2025); S3 ingest_salaries.py (author checked a live roster builder: "MVP $12,000 + 5 FLEX x $8,000 = $52,000 of the $60,000 cap, leaving exactly $8,000"; "1 MVP + 5 FLEX = 6 total"; `min_per_team: 1`); N9 (6 players, mvpSalary, ≤60000, 1–5 per team)]. Change date ~Aug 2025 from tweet IDs, which decode to 2025-08-05 and 2025-09-01 [VERIFIED: snowflake decoding]; tweet text and W1/W4 [UNVERIFIED] |
| NFL Single Game (legacy, pre-2025) | MVP + 4 FLEX (5) | pydfs UTIL = QB/WR/RB/TE/K (no D) | $60,000 | max 4 per team; max 2 QBs | MVP 1.5x points; no salary premium; 17-column file | [VERIFIED: S13 FanDuelSingleGameFootballSettings and FanduelSingleGameMaxQBRule; S14 NFL_MVP roster 5; N8 file]. **Do not use for current contests** |
| **CFB** | QB,RB,RB,WR,WR,WR,**Super FLEX** (7) | Super FLEX = QB/RB/WR/TE; **TEs keep `Position TE` and get `Roster Position WR/Super FLEX`**; no FLEX token | $60,000 [UNVERIFIED] | team rules [UNVERIFIED] | none | [VERIFIED tokens: https://raw.githubusercontent.com/NastyAnalytics/DFS/HEAD/CFB/FD_LU.csv (Oct 2022)] [CORRECTED: was "TEs listed as WRs"]. Slot counts, cap and 2025 availability from W8 snippets [UNVERIFIED] |
| **NBA classic** | PG,PG,SG,SG,SF,SF,PF,PF,C (9) | multi-position via `Roster Position` | $60,000 | max 4 from one team; ≥3 teams | none | [VERIFIED: S7 header contract and config "59000/60000, team limit 4"; S13 FanDuelBasketballSettings] |
| **NBA Single Game (2025-26)** | MVP + 5 UTIL (6) | any | $60,000 | one game | MVP 1.5x points and 1.5x salary | [VERIFIED: S5 Jan 2026 file has `MVP 1.5x Salary` (34/34) and `MVP - 1.5X Points/UTIL`]. The slot count 6 [UNVERIFIED] (RotoWire snippet) |
| NBA Single Game (legacy) | MVP(2x), STAR(1.5x), PRO(1.2x), UTIL, UTIL (5) | any | $60,000 | max 4 per team | points only | [VERIFIED as code: S13, S14 NBA_MVP, N10]. Superseded |
| **MLB** | P, C/1B, 2B, 3B, SS, OF, OF, OF, UTIL (9) | UTIL = any hitter; the C/1B slot takes C or 1B (token `C/1B`) | $35,000 | **max 4 hitters per team; max 5 per team incl. pitcher; ≥3 teams** | none | [VERIFIED: N4 tokens (May 2026); S13 FanDuelBaseballSettings (budget 35000, max_from_one_team 5, FanduelBaseballRosterRule max 4 hitters, min_teams 3); S14 quoting "Up to five players from same team, provided one is a pitcher." from https://support.fanduel.com/s/article/How-many-players-can-I-select-from-one-team (the article itself is [UNVERIFIED])] |
| MLB Single Game | legacy MVP(2x)/STAR(1.5x)/3 UTIL | — | **$35,000 (draftfast) vs $60,000 (pydfs, inherited)** | — | — | [CORRECTED: was "$35,000" with no conflict noted; S13 FanDuelSingleGameBaseballSettings inherits budget 60000; S14 MLB_MVP 35_000]. Current format [UNVERIFIED] |
| **NHL** | C,C,W,W,D,D,UTIL,UTIL,G (9) | UTIL = C/W/D | $55,000 | max 4 per team; ≥3 teams | none | [VERIFIED as code: S13 FanDuelHockeySettings; N5 positions C/W/D/G]. W5 official-guide snippet [UNVERIFIED] |
| NHL Single Game | legacy Captain (1.5x) + 4 UTIL | — | $55,000 (pydfs inherits $60,000 [VERIFIED: S13]) | — | Captain 1.5x | [UNVERIFIED] current format |
| **WNBA** | G,G,G,F,F,F,F (7) | — | $40,000 | max 4 per team; ≥3 teams | none | [VERIFIED as code: S13 FanDuelWnbaSettings, S14] |
| **PGA** | G x6 | golfers | $60,000 | none | none | [VERIFIED as code: S13, S14] |
| **NASCAR** | D x5 | drivers | $50,000 | none | none | [VERIFIED as code: S13, S14] |
| **MMA** | MVP + 5 F | — | `budget = 100` in pydfs | — | MVP (multiplier [UNVERIFIED]) | [VERIFIED as code: S13 FanDuelMMASettings]; whether FD offers MMA [UNVERIFIED] |
| **LoL** | STAR,TOP,JNG,MID,ADC,SUP,TEAM | — | $60,000 | max 4 per team, ≥3 teams | STAR 1.5x [UNVERIFIED] | [VERIFIED as code: S13 FanDuelLOLSettings]; probably discontinued [UNVERIFIED] |
| Soccer, tennis, CS2 | — | | | | | no evidence found for FD |

### 4.4 Captain/MVP mechanics, side by side

| | DK Captain (CPT) | FD MVP (current, by Oct 2025) | FD legacy (2021 files) |
|---|---|---|---|
| Roster | CPT + 5 FLEX/UTIL | MVP + 5 FLEX/UTIL | NFL MVP + 4 FLEX; NBA MVP(2x)+STAR(1.5x)+PRO(1.2x)+2 UTIL |
| Points multiplier | 1.5x [VERIFIED: S9 positionTip] | 1.5x [VERIFIED: S3, S5, S6 Roster Position text] | NFL 1.5x; NBA 2x/1.5x/1.2x [VERIFIED as code: S13, N10] |
| Salary multiplier | 1.5x, pre-applied in the CPT row [VERIFIED: S2, S3, N6] | 1.5x via the `MVP 1.5x Salary` column [VERIFIED: S3, S5, S6] | none (17-column file) [VERIFIED: N8] |
| File shape | two rows per player, separate IDs | one row per player, two salary columns | one row per player |
| Cap | $50,000 [VERIFIED: S9] | $60,000 [VERIFIED as third-party: S3 notes, N9] | $60,000 [VERIFIED as code: S13] |

Lesson for the engine: slot multipliers changed in 2025, and both open-source optimizers checked (pydfs 3.6.1 from 2021; draftfast 3.12.5 from 2024) still encode
the old FD single-game rules [VERIFIED: S13, S14]. Rules must live in a dated, user-confirmed registry and be cross-checked against the file on every run
(assert `MVP 1.5x Salary == 1.5 x Salary`, parse `1.5X` from the Roster Position text, and require the `MVP 1.5x Salary` column to tell current from legacy).

---

## 5. Late swap and entry limits

- **DK Classic:** `allowLateSwap: true` (types 1 and 70; 70 says "Includes Late Swap.") [VERIFIED: S9]. Each player locks at his own game's start: the 20 Oct 2026 entries
  file shows `(LOCKED)` only on players whose games show `In Progress`/`Final` [VERIFIED: N1]. Re-upload the same entries layout ("only include entries you are
  changing") [VERIFIED: S1, S2, N1]. Lock time = `Game Info` (ET) [VERIFIED: S2 README "kickoffs are Eastern"; `In Progress` counts as started].
- **DK Showdown:** `allowLateSwap: false` (81, 96, 159), so the whole lineup locks at the game's start. [VERIFIED: S9]
- **DK NBA late-swap history:** removed Oct 2016, back for 2018-19, occasional "No Late Swap" nights (1 and 15 Mar 2024) [UNVERIFIED: W15 snippets]. Treat late swap as a
  per-contest fact and allow overrides.
- **DK MMA:** late swap removed after a trial [UNVERIFIED: W16 snippet].
- **FD:** late swap lets you "swap out any player in your lineup before his individual game starts. Just look for the icon to find Late Swap slates."
  [UNVERIFIED: W5 snippet]. Added for NFL in 2016 and NBA in 2020 [UNVERIFIED: W17]. The FD player list has no kickoff times and no LOCKED marker [VERIFIED: S3, S5, N4],
  so lock state needs an external schedule (or a time in the template's `Game`, [UNVERIFIED]).
- **Entry limits:** DK contest names carry them (`[150 Entry Max]`, `[20 Entry Max]`, `[Single Entry]`) [VERIFIED: S1, S2, N1]. DK new-lineup template: "You can create up
  to 500 lineups per file" [VERIFIED: S12, Nov 2024]. FD maximum entries per contest: [UNVERIFIED].
- Registering entries before upload (placeholder lineups) [UNVERIFIED: W19 snippets]; DK's own instruction "blank for reservations" implies entries can exist without
  lineups [VERIFIED: S1].

---

## 6. Facts NOT in either CSV, so they must live in the rules registry or per-contest metadata

| Fact | DK salaries | DK entries | FD player list | FD entries | Where it must come from |
|---|---|---|---|---|---|
| Roster slots (labels, order) | no (eligibility only) | **yes** (header) | no | **yes** (header) | entries header if present, else registry |
| Slot eligibility | yes (`Roster Position`) | yes (pool) | yes (`Roster Position`; absent in 2020 files) | yes (pool) | file, else registry |
| Salary cap / salary floor | no | no | no | no | registry (DK min 0 per S9) |
| Min games/teams, max per team, max hitters per team, team-count exclusions (NHL G) | no | no | no | no | registry |
| CPT/MVP points multiplier | no | no | partly (`MVP - 1.5X Points` text) | no | registry, validated against the text |
| CPT/MVP salary multiplier | implicit (CPT rows priced) | implicit | yes (`MVP 1.5x Salary`) | ? | file plus registry check |
| Scoring rules | no | no | no | no | scoring registry (separate topic) |
| Late swap allowed | no | partly (`(LOCKED)` only once games start) | no | no | registry per game type, overridable per contest |
| Lock / kickoff times | **yes** (`Game Info`, ET; becomes `In Progress`/`Final`) | yes | **no** | [UNVERIFIED] | DK file; FD external schedule |
| Field size, payouts, prize pool, rake, contest type | no | no | no | no | per-contest metadata entered by the user |
| Max entries per user | no | partly (contest-name tag) | no | no | per-contest metadata; parse the name as a hint |
| Entry fee, contest ID, contest name | no | **yes** | no | **yes** | entries file |
| Injury status | yes (`Status`, Sep 2026 onward only) | **no** (9-column pool, still on 20 Oct 2026) | yes (`Injury Indicator`) | ? | file, then news research |
| Probable pitcher / batting order | no | no | **MLB only** (cols 14–15) | ? | FD file (MLB); else external |
| Opponent | parse `Game Info` | parse | yes (`Opponent`) | yes | file |

[VERIFIED: compiled from S1–S6, N1–N6]

**Automated access policy (constraint from the brief).** Neither DK's nor FD's ToS or robots.txt could be read [VERIFIED: this session; both hosts blocked], so
whether they permit automated access is [UNVERIFIED]. Treat both as **no automated access**: the engine reads only files the user downloads, and the registry is
filled by hand from the sites' rules pages, with the user confirming each game type once per season or whenever a file disagrees with the registry. The DK API (§4.1)
and FD's fixture-list API are out of bounds. S7 notes that "FanDuel API access can be blocked by anti-bot controls depending on runtime network/session"
[VERIFIED: S7 spec line 115], and S10 says DK "very much does NOT intend to have this API used by the public" [VERIFIED: S10].

---

## 7. Proposed rules-registry schema (YAML)

Design principles (unchanged from the original, with fixes):
1. Key = `site.sport.game_type[.variant]`, one entry per site game type. Per-contest facts go in `contest_meta`.
2. It mirrors DK's rule-object model (`salaryCap`, `gameCount`, `teamCount`, `uniquePlayers`, `allowLateSwap`, `lineupTemplate`).
3. Each entry has a **detection signature** and **provenance** (`sources`, `verified_on`, `confidence`, `expires_on`). If the file disagrees with an entry, the run stops and asks the user.
4. Slot multipliers are explicit, and so is the salary source: `file_row` (DK CPT rows), `file_column` (FD `MVP 1.5x Salary`) or `computed`.
5. **New:** eligibility is matched on `Roster Position` tokens when the file has them (e.g. DK MLB pitcher token `P`, DK CFB `S-FLEX`, FD CFB `Super FLEX`), and on
   `Position` only as a fallback (FD 2020 files have no `Roster Position`).

```yaml
# rules_registry.yaml
schema_version: 1
defaults:
  unique_players: true          # DK uniquePlayers: true (S9)
  salary_min: 0                 # DK salaryCap.minValue 0 (S9)
  timezone_of_file_times: America/New_York   # DK Game Info " ET"

entries:
  - key: <site>.<sport>.<game_type>[.<variant>]
    site: draftkings | fanduel
    sport: nfl | cfb | cfl | nba | wnba | mlb | nhl | pga | mma | nascar | soccer | tennis | cs2 | lol
    game_type: classic | showdown | single_game | tiers | captain | weekend
    site_game_type_id: <int|null>      # DK gameTypeId (1, 96, 70, 81, 159 verified)
    status: active | legacy | unverified
    detect:
      file_kinds: [dk_salaries, dk_entries, dk_upload_template, fd_players, fd_entries, fd_upload_template]
      slot_signature: [<label>, …]     # exact ordered labels from the entries/upload header
      slot_label_aliases: {<alias>: <canonical>}
      roster_position_vocab: [<token>, …]          # tokens allowed in Roster Position
      position_vocab: [<token>, …]                 # tokens allowed in Position
      required_columns: [<header>, …]
      optional_columns: [<header>, …]              # e.g. DK Status; FD Roster Position (absent 2020); FD Probable Pitcher
      distinct_games: {min: <int|null>, max: <int|null>}
      team_universe: nfl32 | cfb | cfl9 | nba30 | wnba | mlb30 | nhl32 | none
    roster:
      size: <int>
      slots:
        - {label: <str>, eligible_tokens: [<roster-position token>, …], eligible_positions: [<position>, …], points_multiplier: 1.0, salary_multiplier: 1.0}
    salary:
      cap: <int|null>                               # null for Tiers
      min_total: 0
      salary_source: file_row | file_column | computed
      salary_column_by_slot: {<slot>: <header>}
    scoring:
      ruleset: <scoring_registry_key>
    constraints:
      min_games: <int|null>
      max_games: <int|null>
      min_teams: <int|null>
      max_teams: <int|null>
      max_players_per_team: <int|null>
      min_players_per_team: <int|null>              # showdown "both teams" => 1
      max_from_team_by_group: [{group: hitters, positions: [C, 1B, 2B, 3B, SS, OF], max: <int>}]
      team_count_excludes_positions: []             # DK NHL: [G]
      max_by_position: {}                           # legacy FD NFL single game: {QB: 2}
      custom: []
    late_swap:
      allowed: <bool>
      lock_granularity: per_game | slate | none
      lock_time_source: dk_game_info | external_schedule | fd_template_game | none
      locked_cell_suffix: " (LOCKED)" | null
      started_game_info_values: ["In Progress", "Final"]   # DK, verified N1
    upload:
      entries_header_prefix: [<str>, …]
      entries_header_prefix_normalized: [<str>, …]
      player_cell_write_format: "{id}"
      player_cell_accepted_formats: ["{id}", "{name} ({id})"]
      locked_cell_write: copy_verbatim             # S2 behaviour; official rule UNVERIFIED
      only_changed_entries_ok: <bool|null>
      max_lineups_per_file: <int|null>
      new_lineup_template_header: [<label>, …]
    provenance:
      sources: [<url>, …]
      verified_on: <YYYY-MM-DD>
      verified_by: research | user
      confidence: high | medium | low
      expires_on: <YYYY-MM-DD>
      notes: <str>

contest_meta_schema:
  contest_id: <str>             # DK "195943226"; FD "127613-279208572" (format from S7; UNVERIFIED on a real FD file)
  site: draftkings | fanduel
  registry_key: <key>
  name: <str>
  entry_fee: <decimal>          # DK "$1 " -> 1.00 (strip "$" and whitespace); FD "0.25"
  field_size: <int|null>
  max_entries_per_user: <int|null>      # hint parsed from "[150 Entry Max]" / "[Single Entry]"
  user_entries: <int>
  contest_type: gpp | cash | double_up | fifty_fifty | h2h | satellite | unknown
  prize_pool: <decimal|null>
  payout_table: [{rank_from: 1, rank_to: 1, prize: 0.0}]
  late_swap_override: <bool|null>
  first_lock_utc: <timestamp>
  source: user_entered | entries_file
```

### 7.1 Filled sample entries (corrected)

```yaml
entries:
  - key: dk.nfl.classic
    site: draftkings
    sport: nfl
    game_type: classic
    site_game_type_id: 1
    status: active
    detect:
      file_kinds: [dk_salaries, dk_entries, dk_upload_template]
      slot_signature: [QB, RB, RB, WR, WR, WR, TE, FLEX, DST]          # VERIFIED S2
      roster_position_vocab: [QB, RB, WR, TE, FLEX, DST]               # VERIFIED S3
      position_vocab: [QB, RB, WR, TE, DST]
      required_columns: [Position, "Name + ID", Name, ID, "Roster Position", Salary, "Game Info", TeamAbbrev, AvgPointsPerGame]
      optional_columns: [Status]
      distinct_games: {min: 2, max: null}
      team_universe: nfl32
    roster:
      size: 9
      slots:
        - {label: QB,   eligible_tokens: [QB]}
        - {label: RB,   eligible_tokens: [RB]}
        - {label: RB,   eligible_tokens: [RB]}
        - {label: WR,   eligible_tokens: [WR]}
        - {label: WR,   eligible_tokens: [WR]}
        - {label: WR,   eligible_tokens: [WR]}
        - {label: TE,   eligible_tokens: [TE]}
        - {label: FLEX, eligible_tokens: [FLEX]}                         # = RB/WR/TE
        - {label: DST,  eligible_tokens: [DST]}
    salary: {cap: 50000, min_total: 0, salary_source: file_row}          # VERIFIED S9
    scoring: {ruleset: dk_nfl_v2026}
    constraints: {min_games: 2, min_teams: 2, max_players_per_team: null}   # VERIFIED S9 (implies <=8)
    late_swap: {allowed: true, lock_granularity: per_game, lock_time_source: dk_game_info, locked_cell_suffix: " (LOCKED)"}
    upload:
      entries_header_prefix: ["Entry ID", "Contest Name", "Contest ID", "Entry Fee"]
      player_cell_write_format: "{id}"
      player_cell_accepted_formats: ["{id}", "{name} ({id})"]
      locked_cell_write: copy_verbatim
      only_changed_entries_ok: true
      max_lineups_per_file: 500                                          # S12 (2024 template)
      new_lineup_template_header: [QB, RB, RB, WR, WR, WR, TE, FLEX, DST]
    provenance:
      sources:
        - https://github.com/JonnyRank/nfl-dfs-optimizer/tree/HEAD/tests/fixtures/public
        - https://github.com/drgregmscott-tech/DFS_Optimizer/tree/HEAD/data/raw_salaries
        - https://github.com/gacolitti/draft.kings/blob/HEAD/tests/testthat/optimize/url/lineups/1/rules.json
        - https://www.draftkings.com/help/rules/1/1     # official; blocked during research
      verified_on: 2026-10-21
      verified_by: research
      confidence: medium              # file formats high; rule counts from a captured API response of unknown date
      expires_on: 2027-08-01

  - key: dk.nfl.showdown
    site: draftkings
    sport: nfl
    game_type: showdown
    site_game_type_id: 96
    status: active
    detect:
      file_kinds: [dk_salaries, dk_entries, dk_upload_template]
      slot_signature: [CPT, FLEX, FLEX, FLEX, FLEX, FLEX]
      roster_position_vocab: [CPT, FLEX]
      position_vocab: [QB, RB, WR, TE, K, DST]
      distinct_games: {min: 1, max: 1}
      team_universe: nfl32
    roster:
      size: 6
      slots:
        - {label: CPT,  eligible_tokens: [CPT],  points_multiplier: 1.5, salary_multiplier: 1.5}
        - {label: FLEX, eligible_tokens: [FLEX]}
        - {label: FLEX, eligible_tokens: [FLEX]}
        - {label: FLEX, eligible_tokens: [FLEX]}
        - {label: FLEX, eligible_tokens: [FLEX]}
        - {label: FLEX, eligible_tokens: [FLEX]}
    salary: {cap: 50000, min_total: 0, salary_source: file_row}          # CPT row pre-priced at 1.5x (VERIFIED 53/53, 55/55)
    scoring: {ruleset: dk_nfl_v2026}
    constraints:
      min_teams: 2
      min_players_per_team: 1         # INVALID_TEAM_COUNT_SD (S9)
      same_athlete_once_across_cpt_and_flex: true    # S2 README: "never both"
    late_swap: {allowed: false, lock_granularity: slate, lock_time_source: dk_game_info, locked_cell_suffix: " (LOCKED)"}
    upload:
      entries_header_prefix: ["Entry ID", "Contest Name", "Contest ID", "Entry Fee"]
      player_cell_write_format: "{id}"   # CPT column gets the CPT-row ID
      player_cell_accepted_formats: ["{id}", "{name} ({id})"]
      only_changed_entries_ok: true
      max_lineups_per_file: 500
    provenance:
      sources:
        - https://github.com/JonnyRank/nfl-dfs-optimizer/blob/HEAD/tests/fixtures/public/DKEntriesShowdown.csv
        - https://github.com/drgregmscott-tech/DFS_Optimizer/blob/HEAD/data/raw_salaries/dk_showdown_wk6_DAL_GB_18Oct2026.csv
        - https://github.com/gacolitti/draft.kings/blob/HEAD/tests/testthat/optimize/url/lineups/96/rules.json
      verified_on: 2026-10-21
      verified_by: research
      confidence: medium
      expires_on: 2027-08-01

  - key: fd.nfl.classic
    site: fanduel
    sport: nfl
    game_type: classic
    status: active
    detect:
      file_kinds: [fd_players, fd_entries, fd_upload_template]
      slot_signature: [QB, RB, RB, WR, WR, WR, TE, FLEX, DEF]          # N10, N11 (third-party)
      roster_position_vocab: [QB, RB, WR, TE, FLEX, DEF]               # VERIFIED S3
      position_vocab: [QB, RB, WR, TE, D]
      required_columns: [Id, Position, "First Name", Nickname, "Last Name", FPPG, Played, Salary, Game, Team, Opponent, "Injury Indicator", "Injury Details", Tier]
      optional_columns: ["Roster Position"]
      distinct_games: {min: 2, max: null}
      team_universe: nfl32
    roster:
      size: 9
      slots:
        - {label: QB,   eligible_tokens: [QB]}
        - {label: RB,   eligible_tokens: [RB]}
        - {label: RB,   eligible_tokens: [RB]}
        - {label: WR,   eligible_tokens: [WR]}
        - {label: WR,   eligible_tokens: [WR]}
        - {label: WR,   eligible_tokens: [WR]}
        - {label: TE,   eligible_tokens: [TE]}
        - {label: FLEX, eligible_tokens: [FLEX], eligible_positions: [RB, WR, TE]}
        - {label: DEF,  eligible_tokens: [DEF],  eligible_positions: [D]}
    salary: {cap: 60000, min_total: 0, salary_source: file_row}
    scoring: {ruleset: fd_nfl_v2026}
    constraints: {min_teams: 3, max_players_per_team: 4}               # pydfs + N11; official UNVERIFIED
    late_swap: {allowed: true, lock_granularity: per_game, lock_time_source: external_schedule, locked_cell_suffix: null}
    upload:
      entries_header_prefix: [entry_id, contest_id, contest_name, entry_fee]   # case/underscore-insensitive; slot-only header also accepted (N11)
      player_cell_write_format: "{id}"        # e.g. 135326-62239
      player_cell_accepted_formats: ["{id}"]  # "{id}:{name}" UNVERIFIED for slot cells
      only_changed_entries_ok: null           # UNVERIFIED
      max_lineups_per_file: null              # UNVERIFIED
    provenance:
      sources:
        - https://github.com/drgregmscott-tech/DFS_Optimizer/blob/HEAD/data/raw_salaries/fd_classic_wk6_main_18Oct2026.csv
        - https://raw.githubusercontent.com/DimaKudosh/pydfs-lineup-optimizer/master/pydfs_lineup_optimizer/sites/fanduel/classic/settings.py
      verified_on: 2026-10-21
      verified_by: research
      confidence: medium
      expires_on: 2027-08-01

  - key: fd.nfl.single_game
    site: fanduel
    sport: nfl
    game_type: single_game
    status: active
    detect:
      file_kinds: [fd_players, fd_entries, fd_upload_template]
      required_columns: [Id, Salary, "MVP 1.5x Salary", Game, "Roster Position"]   # the MVP column is the current-format signal
      roster_position_vocab: ["MVP - 1.5X Points", AnyFLEX]   # same text in legacy 2021 files, so not a format signal
      position_vocab: [QB, RB, WR, TE, K, D]
      distinct_games: {min: 1, max: 1}
      team_universe: nfl32
      slot_signature: null                  # UNVERIFIED; read from template; expect 6 slots, likely "MVP - 1.5X Points" + 5 "AnyFLEX"
      slot_label_aliases: {"MVP - 1.5X Points": MVP, "AnyFLEX": FLEX}
    roster:
      size: 6
      slots:
        - {label: MVP,  eligible_positions: [QB, RB, WR, TE, K, D], points_multiplier: 1.5, salary_multiplier: 1.5}
        - {label: FLEX, eligible_positions: [QB, RB, WR, TE, K, D]}
        - {label: FLEX, eligible_positions: [QB, RB, WR, TE, K, D]}
        - {label: FLEX, eligible_positions: [QB, RB, WR, TE, K, D]}
        - {label: FLEX, eligible_positions: [QB, RB, WR, TE, K, D]}
        - {label: FLEX, eligible_positions: [QB, RB, WR, TE, K, D]}
    salary:
      cap: 60000
      salary_source: file_column
      salary_column_by_slot: {MVP: "MVP 1.5x Salary", FLEX: Salary}
    scoring: {ruleset: fd_nfl_v2026}
    constraints:
      min_teams: 2
      min_players_per_team: 1               # S3 notes + N9 (third-party)
      max_players_per_team: 5               # implied by min 1 per team; legacy 5-man rule was 4
    late_swap: {allowed: false, lock_granularity: slate, lock_time_source: external_schedule}   # UNVERIFIED
    upload:
      entries_header_prefix: [entry_id, contest_id, contest_name, entry_fee]
      player_cell_write_format: "{id}"
    provenance:
      sources:
        - https://raw.githubusercontent.com/umrebel3092/Umreble3092/HEAD/FanDuel-NFL-2025-CDT-10-CDT-19-CDT-121692-players-list.csv
        - https://github.com/drgregmscott-tech/DFS_Optimizer/blob/HEAD/scripts/ingest_salaries.py
        - https://raw.githubusercontent.com/meinhardtmr/NFL-Fantasy/HEAD/2025/Excel/VBA/NFL.bas
      verified_on: 2026-10-21
      verified_by: research
      confidence: medium
      expires_on: 2027-08-01
      notes: "Legacy MVP + 4 FLEX (no salary premium, 17-column file, max 4 per team, max 2 QB) is stored as fd.nfl.single_game.legacy, status: legacy."

  - key: dk.cfb.classic
    site: draftkings
    sport: cfb
    game_type: classic
    status: active
    detect:
      file_kinds: [dk_salaries, dk_entries, dk_upload_template]
      slot_signature: [QB, RB, RB, WR, WR, WR, FLEX, S-FLEX]           # header label UNVERIFIED; token "S-FLEX" VERIFIED (N2, 2022)
      slot_label_aliases: {"SUPER FLEX": S-FLEX, "SUPERFLEX": S-FLEX, "SFLEX": S-FLEX}
      roster_position_vocab: [QB, RB, WR, FLEX, S-FLEX]
      position_vocab: [QB, RB, WR]          # no TE, DST or K (N2)
      distinct_games: {min: 2, max: null}
      team_universe: cfb
    roster:
      size: 8
      slots:
        - {label: QB,     eligible_tokens: [QB]}
        - {label: RB,     eligible_tokens: [RB]}
        - {label: RB,     eligible_tokens: [RB]}
        - {label: WR,     eligible_tokens: [WR]}
        - {label: WR,     eligible_tokens: [WR]}
        - {label: WR,     eligible_tokens: [WR]}
        - {label: FLEX,   eligible_tokens: [FLEX]}       # RB/WR
        - {label: S-FLEX, eligible_tokens: [S-FLEX]}     # QB/RB/WR
    salary: {cap: 50000, min_total: 0, salary_source: file_row}        # cap UNVERIFIED
    scoring: {ruleset: dk_cfb_v2026}
    constraints:
      min_games: 2                          # pydfs; UNVERIFIED officially
      min_teams: 2                          # UNVERIFIED (DK classic default)
    late_swap: {allowed: true, lock_granularity: per_game, lock_time_source: dk_game_info, locked_cell_suffix: " (LOCKED)"}   # UNVERIFIED for CFB
    upload:
      entries_header_prefix: ["Entry ID", "Contest Name", "Contest ID", "Entry Fee"]
      player_cell_write_format: "{id}"
      player_cell_accepted_formats: ["{id}", "{name} ({id})"]
      max_lineups_per_file: 500
    provenance:
      sources:
        - https://raw.githubusercontent.com/NastyAnalytics/DFS/HEAD/CFB/DKSalaries.csv
        - https://raw.githubusercontent.com/DimaKudosh/pydfs-lineup-optimizer/master/pydfs_lineup_optimizer/sites/draftkings/classic/settings.py
      verified_on: 2026-10-21
      verified_by: research
      confidence: medium
      expires_on: 2027-08-01

  - key: fd.cfb.classic
    site: fanduel
    sport: cfb
    game_type: classic
    status: unverified
    detect:
      file_kinds: [fd_players, fd_entries]
      roster_position_vocab: [QB, RB, WR, "Super FLEX"]   # VERIFIED N3 (2022)
      position_vocab: [QB, RB, WR, TE]                    # TE keeps Position TE, gets WR/Super FLEX
      distinct_games: {min: 2, max: null}
      team_universe: cfb
    roster:
      size: 7                                             # UNVERIFIED (W8)
      slots:
        - {label: QB,           eligible_tokens: [QB]}
        - {label: RB,           eligible_tokens: [RB]}
        - {label: RB,           eligible_tokens: [RB]}
        - {label: WR,           eligible_tokens: [WR]}    # includes TEs
        - {label: WR,           eligible_tokens: [WR]}
        - {label: WR,           eligible_tokens: [WR]}
        - {label: "Super FLEX", eligible_tokens: ["Super FLEX"]}
    salary: {cap: 60000, min_total: 0, salary_source: file_row}   # UNVERIFIED
    scoring: {ruleset: fd_cfb_v2026}
    constraints: {}                                       # UNVERIFIED
    provenance:
      sources: [https://raw.githubusercontent.com/NastyAnalytics/DFS/HEAD/CFB/FD_LU.csv]
      verified_on: 2026-10-21
      verified_by: research
      confidence: low
      expires_on: 2027-08-01

  - key: dk.nba.classic
    site: draftkings
    sport: nba
    game_type: classic
    site_game_type_id: 70
    status: active
    detect:
      file_kinds: [dk_salaries, dk_entries, dk_upload_template]
      slot_signature: [PG, SG, SF, PF, C, G, F, UTIL]
      roster_position_vocab: [PG, SG, SF, PF, C, G, F, UTIL]
      position_vocab: [PG, SG, SF, PF, C]
      distinct_games: {min: 2, max: null}
      team_universe: nba30
    roster:
      size: 8
      slots:
        - {label: PG,   eligible_tokens: [PG]}
        - {label: SG,   eligible_tokens: [SG]}
        - {label: SF,   eligible_tokens: [SF]}
        - {label: PF,   eligible_tokens: [PF]}
        - {label: C,    eligible_tokens: [C]}
        - {label: G,    eligible_tokens: [G]}
        - {label: F,    eligible_tokens: [F]}
        - {label: UTIL, eligible_tokens: [UTIL]}
    salary: {cap: 50000, min_total: 0, salary_source: file_row}
    scoring: {ruleset: dk_nba_v2026}
    constraints: {min_games: 2, min_teams: 2}
    late_swap: {allowed: true, lock_granularity: per_game, lock_time_source: dk_game_info, locked_cell_suffix: " (LOCKED)", started_game_info_values: ["In Progress", "Final"]}
    upload:
      entries_header_prefix: ["Entry ID", "Contest Name", "Contest ID", "Entry Fee"]
      player_cell_write_format: "{id}"
      player_cell_accepted_formats: ["{id}", "{name} ({id})"]
      locked_cell_write: copy_verbatim
      only_changed_entries_ok: true
      max_lineups_per_file: 500
    provenance:
      sources:
        - https://raw.githubusercontent.com/troyseanwilliams-hash/Fantasy-Lineup-Optimizer/main/attached_assets/DKEntries_(17)_1773687635103.csv
        - https://raw.githubusercontent.com/JonnyRank/nba-dfs-optimizer/HEAD/tests/fixtures/2026-10-20/Post-L0-DKEntries.csv
        - https://github.com/gacolitti/draft.kings/blob/HEAD/tests/testthat/optimize/url/lineups/70/rules.json
      verified_on: 2026-10-21
      verified_by: research
      confidence: high                      # slots/eligibility/LOCKED from real 2026 files; cap/counts from S9 (medium)
      expires_on: 2027-10-01
      notes: "Late swap may be disabled on specific nights (W15, UNVERIFIED); allow a per-contest override."

  - key: fd.nba.classic
    site: fanduel
    sport: nba
    game_type: classic
    status: active
    detect:
      file_kinds: [fd_players, fd_entries]
      slot_signature: [PG, PG, SG, SG, SF, SF, PF, PF, C]
      roster_position_vocab: [PG, SG, SF, PF, C]
      position_vocab: [PG, SG, SF, PF, C]
      distinct_games: {min: 2, max: null}
      team_universe: nba30
    roster:
      size: 9
      slots:
        - {label: PG, eligible_tokens: [PG]}
        - {label: PG, eligible_tokens: [PG]}
        - {label: SG, eligible_tokens: [SG]}
        - {label: SG, eligible_tokens: [SG]}
        - {label: SF, eligible_tokens: [SF]}
        - {label: SF, eligible_tokens: [SF]}
        - {label: PF, eligible_tokens: [PF]}
        - {label: PF, eligible_tokens: [PF]}
        - {label: C,  eligible_tokens: [C]}
    salary: {cap: 60000, min_total: 0, salary_source: file_row}
    scoring: {ruleset: fd_nba_v2026}
    constraints: {min_teams: 3, max_players_per_team: 4}
    late_swap: {allowed: true, lock_granularity: per_game, lock_time_source: external_schedule, locked_cell_suffix: null}
    upload:
      entries_header_prefix: [entry_id, contest_id, contest_name, entry_fee]
      player_cell_write_format: "{id}"
      player_cell_accepted_formats: ["{id}"]
    provenance:
      sources:
        - https://github.com/dschrengost/projections-v2/blob/HEAD/docs/pipeline/FANDUEL_INTEGRATION_SPEC.md
        - https://raw.githubusercontent.com/jackdegen/nba-props/HEAD/data/current-fanduel.csv
        - https://raw.githubusercontent.com/DimaKudosh/pydfs-lineup-optimizer/master/pydfs_lineup_optimizer/sites/fanduel/classic/settings.py
      verified_on: 2026-10-21
      verified_by: research
      confidence: medium
      expires_on: 2027-10-01

  - key: dk.mlb.classic
    site: draftkings
    sport: mlb
    game_type: classic
    status: active
    detect:
      file_kinds: [dk_salaries, dk_entries, dk_upload_template]
      slot_signature: [P, P, C, 1B, 2B, 3B, SS, OF, OF, OF]
      roster_position_vocab: [P, C, 1B, 2B, 3B, SS, OF]   # pitchers carry token P (N7)
      position_vocab: [SP, RP, C, 1B, 2B, 3B, SS, OF]
      distinct_games: {min: 2, max: null}
      team_universe: mlb30
    roster:
      size: 10
      slots:
        - {label: P,  eligible_tokens: [P]}
        - {label: P,  eligible_tokens: [P]}
        - {label: C,  eligible_tokens: [C]}
        - {label: 1B, eligible_tokens: [1B]}
        - {label: 2B, eligible_tokens: [2B]}
        - {label: 3B, eligible_tokens: [3B]}
        - {label: SS, eligible_tokens: [SS]}
        - {label: OF, eligible_tokens: [OF]}
        - {label: OF, eligible_tokens: [OF]}
        - {label: OF, eligible_tokens: [OF]}
    salary: {cap: 50000, min_total: 0, salary_source: file_row}
    scoring: {ruleset: dk_mlb_v2026}
    constraints:
      min_games: 2
      max_from_team_by_group:
        - {group: hitters, positions: [C, 1B, 2B, 3B, SS, OF], max: 5}
    late_swap: {allowed: true, lock_granularity: per_game, lock_time_source: dk_game_info, locked_cell_suffix: " (LOCKED)"}
    upload:
      entries_header_prefix: ["Entry ID", "Contest Name", "Contest ID", "Entry Fee"]
      player_cell_write_format: "{id}"
      player_cell_accepted_formats: ["{id}", "{name} ({id})"]
      max_lineups_per_file: 500
    provenance:
      sources:
        - https://raw.githubusercontent.com/shawtes/mlb-draftkings-system/HEAD/docs/archive/DRAFTKINGS_FORMAT_FIX_SUMMARY.md
        - https://raw.githubusercontent.com/JonPGH/dailyMatchups/HEAD/dksalaries_early.csv
        - https://raw.githubusercontent.com/DimaKudosh/pydfs-lineup-optimizer/master/pydfs_lineup_optimizer/rules.py
      verified_on: 2026-10-21
      verified_by: research
      confidence: medium
      expires_on: 2027-03-01

  - key: fd.mlb.classic
    site: fanduel
    sport: mlb
    game_type: classic
    status: active
    detect:
      file_kinds: [fd_players, fd_entries]
      slot_signature: [P, C/1B, 2B, 3B, SS, OF, OF, OF, UTIL]   # labels UNVERIFIED in a template; tokens VERIFIED (N4)
      roster_position_vocab: [P, C, 1B, 2B, 3B, SS, OF, UTIL]   # cells like "C/1B/UTIL" split on "/"; the C/1B slot is matched via eligible_positions
      position_vocab: [P, C, 1B, 2B, 3B, SS, OF]
      required_columns: [Id, Position, Nickname, Salary, Game, Team, Opponent, "Probable Pitcher", "Batting Order", "Roster Position"]
      team_universe: mlb30
    roster:
      size: 9
      slots:
        - {label: P,    eligible_positions: [P]}
        - {label: C/1B, eligible_positions: [C, 1B]}
        - {label: 2B,   eligible_positions: [2B]}
        - {label: 3B,   eligible_positions: [3B]}
        - {label: SS,   eligible_positions: [SS]}
        - {label: OF,   eligible_positions: [OF]}
        - {label: OF,   eligible_positions: [OF]}
        - {label: OF,   eligible_positions: [OF]}
        - {label: UTIL, eligible_positions: [C, 1B, 2B, 3B, SS, OF]}
    salary: {cap: 35000, min_total: 0, salary_source: file_row}
    scoring: {ruleset: fd_mlb_v2026}
    constraints:
      min_teams: 3
      max_players_per_team: 5
      max_from_team_by_group:
        - {group: hitters, positions: [C, 1B, 2B, 3B, SS, OF], max: 4}
    provenance:
      sources:
        - https://raw.githubusercontent.com/alturn84/BARREL-PROOF-FLASK/HEAD/FanDuel/FanDuel-MLB-2026%20EDT-05%20EDT-18%20EDT-130513-players-list.csv
        - https://raw.githubusercontent.com/DimaKudosh/pydfs-lineup-optimizer/master/pydfs_lineup_optimizer/rules.py
        - https://github.com/BenBrostoff/draftfast/blob/master/draftfast/rules.py
      verified_on: 2026-10-21
      verified_by: research
      confidence: medium
      expires_on: 2027-03-01

  - key: dk.pga.classic
    site: draftkings
    sport: pga
    game_type: classic
    status: active
    detect:
      file_kinds: [dk_salaries, dk_entries, dk_upload_template]
      slot_signature: [G, G, G, G, G, G]
      roster_position_vocab: [G]
      position_vocab: [G]
      team_abbrev_literal: Golf
      game_info_is_event_name: true
      team_universe: none
    roster:
      size: 6
      slots: [{label: G, eligible_tokens: [G]}, {label: G, eligible_tokens: [G]}, {label: G, eligible_tokens: [G]},
              {label: G, eligible_tokens: [G]}, {label: G, eligible_tokens: [G]}, {label: G, eligible_tokens: [G]}]
    salary: {cap: 50000, min_total: 0, salary_source: file_row}
    scoring: {ruleset: dk_pga_v2026}
    constraints: {}
    late_swap: {allowed: false, lock_granularity: slate, lock_time_source: external_schedule}   # UNVERIFIED
    upload:
      entries_header_prefix: ["Entry ID", "Contest Name", "Contest ID", "Entry Fee"]
      player_cell_write_format: "{id}"
      max_lineups_per_file: 500
    provenance:
      sources:
        - https://raw.githubusercontent.com/travismangone/matchup-terminal/HEAD/data/dk_salaries.csv
        - https://github.com/BenBrostoff/draftfast/blob/master/draftfast/constants/roster_size.py
      verified_on: 2026-10-21
      verified_by: research
      confidence: medium
      expires_on: 2027-01-01

  - key: dk.mma.classic
    site: draftkings
    sport: mma
    game_type: classic
    status: active
    detect:
      file_kinds: [dk_salaries, dk_entries, dk_upload_template]
      slot_signature: [F, F, F, F, F, F]
      roster_position_vocab: [F]
      position_vocab: [F]
      team_universe: none
    roster:
      size: 6
      slots: [{label: F, eligible_tokens: [F]}, {label: F, eligible_tokens: [F]}, {label: F, eligible_tokens: [F]},
              {label: F, eligible_tokens: [F]}, {label: F, eligible_tokens: [F]}, {label: F, eligible_tokens: [F]}]
    salary: {cap: 50000, min_total: 0, salary_source: file_row}
    scoring: {ruleset: dk_mma_v2026}
    constraints:
      custom:
        - "Rostering both fighters of one bout: no rule found against it (UNVERIFIED)."
    late_swap: {allowed: false, lock_granularity: slate, lock_time_source: dk_game_info}   # W16, UNVERIFIED
    upload:
      entries_header_prefix: ["Entry ID", "Contest Name", "Contest ID", "Entry Fee"]
      player_cell_write_format: "{id}"
      max_lineups_per_file: 500
    provenance:
      sources:
        - https://raw.githubusercontent.com/DimaKudosh/pydfs-lineup-optimizer/master/pydfs_lineup_optimizer/sites/draftkings/classic/settings.py
      verified_on: 2026-10-21
      verified_by: research
      confidence: low                       # S16 is synthetic; rules from 2021 code
      expires_on: 2027-01-01
```

Further entries in the same shape (values from §4.2–§4.3, with the tags noted there): `dk.nba.showdown` (81: CPT + 5 UTIL, 1.5x/1.5x verified, both teams, no late swap),
`dk.cfb.showdown` (UNVERIFIED), `fd.nba.single_game` (MVP + 5 UTIL, `MVP 1.5x Salary`), `dk.nhl.classic` (C,C,W,W,W,D,D,G,UTIL; ≥3 teams excluding G),
`fd.nhl.classic` (C,C,W,W,D,D,UTIL,UTIL,G; $55,000), and the `legacy` FD single-game variants (status: legacy, only for parsing old files).

---

## 8. Parser design: auto-detect site, sport and game type from the file alone

### 8.1 Reading

1. Read bytes and decode `utf-8-sig` (strips DK's BOM when present; many files have none), falling back to `cp1252`. Use `csv.reader` with `newline=''`
   (CRLF and LF both occur, rows are jagged, quoted names contain commas). Keep raw rows. Never use pandas inference for detection (FD has blank header names).
2. Normalise each header cell: `strip()`, collapse whitespace; keep an exact form and `norm(x) = lower(x).replace('_',' ')`.

### 8.2 File-kind classification (first rule that matches wins)

| Rule | Test on rows | Kind |
|---|---|---|
| A | `norm(row0[0:4]) == ["entry id","contest name","contest id","entry fee"]` | **dk_entries** (do not require `Instructions`) |
| B | `norm(row0[0:4]) == ["entry id","contest id","contest name","entry fee"]` | **fd_entries** |
| C | `row0[0:9] == ["Position","Name + ID","Name","ID","Roster Position","Salary","Game Info","TeamAbbrev","AvgPointsPerGame"]` (optional `Status`, other extras allowed) | **dk_salaries** |
| D | `row0[0:4] == ["Id","Position","First Name","Nickname"]` and `"FPPG"` in row0 (`Roster Position` optional) | **fd_players** (`single_game_current` if `"MVP 1.5x Salary"` in row0; `single_game_candidate` if only the Roster Position text starts with `MVP - `) |
| E | the cells before the first blank in row0 are all slot tokens, and some row contains `"Name + ID"` | **dk_upload_template** |
| F | the cells before the first blank in row0 are all slot tokens, and some row contains `"Player ID + Player Name"` | **fd_upload_template** (covers N11's slot-only layout) |
| G | the first 30 rows contain a row with `"TeamAbbrev"` (DK) or `"Nickname"`+`"FPPG"` (FD) | embedded pool, unknown wrapper; continue with a warning |
| else | — | **ask the user** |

### 8.3 Locating the embedded player pool

- DK: find the first row containing `"Name + ID"`; `c = row.index("Position")` (= the `Instructions` column, or 0 for a standalone export). Read the header `r[c:]` up to the first
  blank and map **by name**. Read the following rows' `[c:]` cells while `row[c]` is non-empty. (Row 8 in every real file seen; search, don't hard-code.)
- FD: find the first row containing `"Player ID + Player Name"`; `c` = its index (14 in the S7/S8 contract); map by name.
- Validate: DK `ID` all digits and `Name + ID` ends with `(<ID>)` (strip first; DST and CS:GO names can carry spaces). FD `Id` matches `^\d+-\d+$`, all prefixes identical,
  and an FD entries file and players list must share the prefix (else "player list is for a different slate").

### 8.4 Reading roster slots from the entries/template header

```
meta_len = 4 for *_entries, 0 for *_upload_template
slots = []
for cell in row0[meta_len:]:
    if cell.strip() == "" or cell.strip() == "Instructions": break
    slots.append(cell.strip())
```

- Keep duplicates; assign internal keys by occurrence (`RB1`, `RB2`, as S7 does with `PG1`, `PG2`). The writer emits the original labels in order.
- Entry rows: `row[0].strip().isdigit()`. Pad short rows.
- Cell decode: DK `^(?P<name>.*?)\s*\((?P<id>\d+)\)(?P<locked>\s*\(LOCKED\))?\s*$` (S2 uses `(\s*\((\d+|LOCKED)\))+\s*$`); FD `^(?P<id>\d+-\d+)$` or
  `^(?P<id>\d+-\d+):(?P<name>.+)$`; blank = reservation.

### 8.5 Site, sport and game-type detection

1. **Site** from the file kind.
2. **Distinct games** `G`: DK = distinct `Game Info` containing `@` (ignore `In Progress`/`Final`; **in a mid-slate entries file, started games collapse to those
   strings, so count games from the earliest download or from `TeamAbbrev` pairs**); FD = distinct `Game`. `G == 1` → showdown/single-game candidates.
   DK showdown is also signalled by `Roster Position ∈ {CPT, FLEX|UTIL}` with exactly half the rows CPT. FD current single game by the `MVP 1.5x Salary` column.
3. **Sport** from the vocabulary, then the team universe:

| Signal | Sport |
|---|---|
| `DST` (DK) or `D` with `Roster Position` `DEF`/`AnyFLEX` (FD), plus `TE` and/or `K` | NFL |
| DK: QB/RB/WR only, tokens `S-FLEX`; FD: token `Super FLEX`; or teams outside NFL-32 (`BAMA`, `MINN`) | CFB |
| `PG`/`SG`/`SF`/`PF`/`C` | NBA |
| basketball tokens with the WNBA team set or May–Oct dates | WNBA |
| DK `SP`/`RP` Position with token `P`; FD `P` plus `C/1B` tokens or a `Probable Pitcher` column | MLB |
| DK `LW`/`RW` with `W/UTIL`; FD `C`/`W`/`D`/`G` | NHL |
| `Position=G`, `TeamAbbrev=Golf`, `Game Info` without `@` | PGA |
| all `F`, fighter-vs-fighter `Game Info` | MMA (DK) |
| all `D` (drivers), event-name `Game Info` | NASCAR |
| `GK`/`D`/`M`/`F` | Soccer |
| `Position=P`, individual matchups | Tennis (DK) |
| `Position=P` with CPT/FLEX and esports tags (mixed-case abbreviations) | CS2 |
| `TOP`/`JNG`/`MID`/`ADC`/`SUP`/`TEAM` | LoL |

   Ambiguity: `G`, `D`, `F` and `P` are overloaded. Combine vocabulary, `Roster Position` tokens, team universe, calendar month and slot signature; if two sports remain, ask.
4. **Game type**: match `(site, sport, slot_signature)` (aliases applied); without an entries header, use `(site, sport, distinct_games, roster_position_vocab)`.
5. **Registry lookup**: exactly one active entry must match. Then run the consistency checks and stop on failure:
   - roster size = slot count; every slot has at least one eligible pool player;
   - DK Showdown: every CPT row has a FLEX/UTIL twin with the same stripped `Name` and `TeamAbbrev`, and `CPT Salary == 1.5 x FLEX Salary` (exact in 162/162 pairs checked);
   - FD single game: the `MVP 1.5x Salary` column exists and equals `1.5 x Salary` on every row (exact in 149/149 rows checked), and the multiplier parsed from `Roster Position`
     (`MVP - (\d+(\.\d+)?)X Points`) equals the registry `points_multiplier`. **If the column is missing, the format is legacy (stop and ask);**
   - cap sanity: the sum of the n cheapest legal players ≤ cap ≤ the sum of the n most expensive;
   - `provenance.expires_on` not passed, else warn "re-verify rules".
6. **Fallbacks**: (i) no signature match → show the detected slots, vocabulary and games, propose the nearest entry, and ask for the cap and constraints, saving
   `status: unverified`; (ii) multiplier mismatch → stop (a likely site rule change, as at FD in 2025); (iii) missing `Status`, missing FD `Roster Position`, or extra
   columns → continue and keep the extras.

### 8.6 Writing upload files

- DK entries: header = original `row0[:4+n]` (S2 does this). Rows = original A–D verbatim + one cell per slot. **Locked slots: copy the downloaded cell verbatim** (S2 behaviour).
  Showdown: the CPT-row ID in the CPT column.
- DK new lineups: header = slot labels; one row of IDs per lineup; ≤500 rows.
- FD entries: header = original row0 up to the last slot, keeping duplicate labels; cells = bare `Id`.
- Every writer re-reads its own output and re-validates it with the solver's rule checker before handing the file to the user.

---

## 9. Implications for the engine

1. Make the entries file the canonical input: it carries the slots, contest IDs, fees, existing lineups, LOCKED state (DK) and the full pool.
   **Caveat:** the DK entries pool has no `Status` column even in Oct 2026, so injury status must come from the standalone salary export or from research.
2. Keep the rules out of the code: one YAML registry with dated provenance, user confirmation and runtime file-vs-registry assertions.
3. Showdown differs by site: on DK, two rows and two IDs with a pre-multiplied salary; on FD, one row with two salary columns. Normalise both to
   `(athlete, slot_role, salary, points_multiplier, site_id)`.
4. Lock times: DK gives them in `Game Info` (ET), and they turn into `In Progress`/`Final` once games start. FD gives none; FD late swap needs a schedule source
   that permits automated access.
5. IDs: DK IDs change per draft group, and FD Id prefixes change per slate. FD's suffix is persistent (even from college to NFL), so use it as a stable key for FD.
   For DK, join by name, team and position with an override table.
6. Data-quality traps to code for: the BOM present in some exports only; CRLF vs LF; the DST trailing space in entries pools; CS:GO leading spaces; mixed-case esports
   abbreviations; FD's blank-named columns (named in MLB); FD's `Roster Position` missing in old files; the fee's trailing space; `$` vs no `$`; `FPPG` blank vs `0`;
   `AvgPointsPerGame` `0` for stars back from injury (Kyrie Irving at $3,000).
7. Nothing in this file was confirmed against an official DK or FD rules page, ToS or robots.txt (all blocked). Before the first real contest, the user should open
   each game type's rules page once and confirm the registry values marked medium, low or UNVERIFIED.
