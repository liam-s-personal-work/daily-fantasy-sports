# Adversarial code review (2026-10-21)

**How it worked:**

- Five independent reviewers each attacked one area of the engine.
- Each could report only defects it had demonstrated by running code: a script, a crafted file or a
  failing assertion.
- A second, skeptical agent per area then re-ran every finding against the current code.
- Fixes were applied while the review ran, nearly all with a regression test. The key ones were checked
  to fail on the old code.
- What the verifiers still found open was then fixed as well. That included one regression the first
  round of fixes had introduced (files-4) and paths the first fixes had missed (for example
  blind_leakage-3).

**78 findings:** critical 14, major 33, minor 31. The areas overlap, so some defects appear twice.

**Outcome:**

- fixed: 63
- fixed after verification: 13
- not a defect: 1
- known limit: 1

Tests: 334 offline tests pass after the fixes (`python -m pytest -q`).

## Contest files, rules and uploads

| id | severity | finding | outcome |
|---|---|---|---|
| files-1 | critical | player_key maps every position "D" to TEAM/dst/DST, so in NHL (and FD NHL, DK soccer and DK NASCAR) all of a team's defensemen, defenders or drivers become one player. | fixed, confirmed by the verifier |
| files-2 | critical | When a game has started, DK prints 'In Progress' or 'Final' in Game Info (per the repo's own research, docs/research/site_formats.md:151,441). _parse_game_info returns (None, None), so started players get start_time=None and game=None and are treated as not started. | fixed, confirmed by the verifier |
| files-3 | critical | Entry lineups, and the new pipeline.safety_lineups through entry_lock_groups (pipeline.py:48-69), lock only cells the file marked '(LOCKED)'. A started player already in an entry but not marked is swapped out, and upload_rows (upload.py:49-51) checks only the file markers, so it does not catch this. | fixed, confirmed by the verifier |
| files-4 | critical | The last call re-solves with lock_keys only, ignoring slot positions, so locked (started) players can move to another cell. upload_final.csv then changes a locked cell. | fixed after verification (b2a0d0a) |
| files-5 | major | The last call crashes when a pick whose game has started is ruled out. The pick is popped from proj (line 97-98) and placed in both lock_keys and exclude_keys, so solve raises Infeasible. | fixed, confirmed by the verifier |
| files-6 | major | The last call writes a single lineup (report 'final') to the first entry only. Other entries are left out, and the call crashes with UploadError when entry 1 has a LOCKED cell that 'final' does not match. | fixed, confirmed by the verifier |
| files-7 | major | FD player lists and entries carry no kickoff times, so every FD Player has start_time=None. The registry marks FD contests late_swap: true, but nothing ever locks or excludes started FD players. The uncommitted pipeline change fills kickoffs only from the NFL provider (ctx.kickoffs is set only in nfl_provider.py). | fixed after verification (b2a0d0a) |
| files-8 | major | A late-swap run always first builds a from-scratch 'final' lineup from unstarted players only. When the remaining games cannot satisfy min_games, the run crashes even though the LOCKED entries could be re-solved legally. | fixed, confirmed by the verifier |
| files-9 | critical | DK MLB's maximum of 5 hitters from one team is not enforced anywhere (the registry note says so). The solver and check_legal both accept lineups DK rejects. | fixed, confirmed by the verifier |
| files-10 | major | The NASCAR branch is unreachable: {'D'} is a subset of the football position set, so a DK NASCAR file is detected as CFB classic. Combined with files-1, the drivers also collapse into one key per team. | fixed, confirmed by the verifier |
| files-11 | major | A multi-contest entries file that mixes cash and tournament contests is labelled GPP as a whole, so cash entries (Double Up, 50/50) get tournament-objective lineups. | fixed, confirmed by the verifier |
| files-12 | minor | norm_name strips generational suffixes, so two different same-team, same-position players ('Marvin Harrison Jr.' and 'Marvin Harrison') share one key. The first row's Player is kept and the solver buys the cheaper ID. | fixed, confirmed by the verifier |
| files-13 | minor | Name-based user locks/excludes (_resolve_names), and lateswap._keys_for for ruled_out, match every player with that display name, so 'Mike Williams' locks or excludes both Mike Williamses. | fixed, confirmed by the verifier |
| files-14 | minor | User-rule team codes are compared verbatim, with no alias mapping and no unknown-team check: exclude_teams ['JAC'] on a DK file using 'JAX' is silently ignored, and stack {'team':'JAC'} gives a generic INFEASIBLE. | fixed, confirmed by the verifier |
| files-15 | minor | write_upload opens and truncates the target before upload_rows validates, so an UploadError leaves a 0-byte upload file on disk. | fixed, confirmed by the verifier |
| files-16 | minor | Files are read as UTF-8 only. A contest CSV re-saved by Excel (cp1252) crashes with a raw UnicodeDecodeError instead of a ContestFileError or a fallback. | fixed, confirmed by the verifier |
| files-17 | minor | An FD single-game player list without a 'Roster Position' column (the code says the column is optional for 2020 files) is detected as classic, with no warning that a classic contest has one game. | not a defect (the illegal lineup cannot happen); a warning was added anyway |
| files-18 | minor | Football sport detection uses only the team set, so a CFB showdown between schools whose codes are also NFL codes (CIN@HOU, both Big 12; ESPN codes ARI/BUF/CIN/HOU/MIA all collide) is detected as NFL. | fixed, confirmed by the verifier |
| files-19 | minor | Uploads from player lists or salary files (no entries) write the registry's canonical slot labels, not the site's labels: FD single game gets 'MVP,FLEX,...' and DK CFB gets 'SUPERFLEX'. The repo's own research (docs/research/site_formats.md §3.2, line 42) records FD's legacy single-game header as 'MVP - 1.5X Points,AnyFLEX,...' (verified in three codebases) and DK CFB's token as 'S-FLEX'. | known limit (PLAN §8: use the site's own entries/upload template) |

## Optimisation and statistics

| id | severity | finding | outcome |
|---|---|---|---|
| math-1 | critical | player_key() treats position 'D' as a team defense, so on NHL (and DK soccer) files every defenseman on a team collapses into one Player 'TEAM/dst/DST'. The engine then uploads a different, cheap defenseman than the one it projected. | fixed, confirmed by the verifier |
| math-2 | major | Pair-role correlations are given to every listed depth player (3 QBs x 6-10 WRs at 0.35 each, and so on). On every real NFL slate the spec matrix is badly indefinite, and the one-step eigenvalue clip shrinks the correlations that matter for stacking. | fixed, confirmed by the verifier |
| math-3 | major | FanDuel defenses have position 'D', but the correlation table, calibration table and learned blend weights are keyed 'DST'. On every FD NFL contest the defense gets default correlations, default calibration and the prior blend weight. | fixed, confirmed by the verifier |
| math-4 | major | Sim-optimal ('sim'/'half') candidates keep Lineup.mean = their score on the very draw they were optimised for, not their projected mean. That number goes to the agents, the report, the grade and the fm=None ranking. | fixed, confirmed by the verifier |
| math-5 | major | The errors fed to update_blend compare the forecaster's median ('likely', raw and uncalibrated) with the baseline's mixture mean, on played-only rows. The encompassing-regression slope is therefore strongly biased against the forecaster. | fixed, confirmed by the verifier |
| math-6 | major | read_standings ignores the 'Roster Position' column. In showdown/single-game standings the CPT/MVP row and the FLEX row of the same player collide on name, and whichever row comes last sets both FPTS and %Drafted. | fixed, confirmed by the verifier |
| math-7 | major | The user's rules lineup is solved with the strategist/checker exclusions added to opts.exclude_keys. If an agent cuts a player the user locked, every solve raises 'players both locked and excluded' and the user gets no rules lineup at all. | fixed, confirmed by the verifier |
| math-8 | minor | The field generator solves forced-stack lineups (qb_stack=1) after dominance pruning, which select._can_prune itself documents as unsafe for stack rules. Some stacked rival lineups are therefore not optimal on their own noisy projections. | fixed, confirmed by the verifier |
| math-9 | minor | The _POOLS memo key covers only source_file, the rounded means, exclusions, user rules, slot locks and seed. It misses the field model and the distribution shape (p10/p50/p90/p_zero), so stale pools and draws come back when those change. | fixed, confirmed by the verifier |
| math-10 | minor | Backup-review point adjustments only rewrite Projection.mean. strategy.candidates simulates its candidate draws from the unchanged quantiles, so the sim-optimal candidates ignore the adjustment. | fixed, confirmed by the verifier |
| math-11 | minor | calibrate_noise stores the first pilot under pools[sigma0] but looks it up as pools[exp(log(sigma0))]. When sigma0 = min(0.3, max_sigma) does not round-trip exactly (0.1, 0.35, ...), build_field crashes with a KeyError. | fixed after verification (b2a0d0a) |
| math-12 | minor | update_blend collapses the weight to 0 instead of keeping the prior when the forecast carries no information (sxx == 0). | fixed, confirmed by the verifier |
| math-13 | minor | UserRules accepts rules the engine never enforces. max_exposure is a declared field that nothing reads, and unknown keys (bring_back, qb_stack, typos) are silently dropped because the model allows extra fields. | fixed, confirmed by the verifier |
| math-14 | minor | three_drafts and top_candidates_by_slot always build from _base(set(), None, None). In a late-swap run, the drafts and the top-3 table can include players whose games have started, who can no longer be added. | fixed, confirmed by the verifier |

## Information leakage and the forecaster's blind

| id | severity | finding | outcome |
|---|---|---|---|
| blind_leakage-1 | critical | The research summary (and each fact's subject and source_url) goes into the blinded forecaster's prompt unfiltered. The research agent is also handed the betting line, so the engine passes its own line through research to the forecaster. | fixed, confirmed by the verifier |
| blind_leakage-2 | critical | The blind_text regexes miss many common ways of writing salaries, site averages, past fantasy points and betting lines, so such facts pass agents_blind and reach the forecaster. | fixed after verification (bee5a67) |
| blind_leakage-3 | critical | Nothing is point-in-time for research. A run on a past slate (`--now` before kickoff) with web research on, which is the CLI default, hands the forecaster the actual post-game box scores of the game it is forecasting. | fixed after verification (bee5a67) |
| blind_leakage-4 | major | The forecaster's usage text prints the weekly-roster status, which on downloaded past weeks is the game-day INA (inactive) designation. Replays and the forecast experiment therefore show the forecaster inactives hours before they are announced, while the baseline deliberately ignores them (nfl.py use_inactives=False). This inflates the learned forecast weights. | fixed, confirmed by the verifier |
| blind_leakage-5 | major | `dfs run --now` on a past slate uses data/learned/blend.json and calibration.json, which were fit on graded 2026 weeks 3-6, so any test of those weeks through the CLI uses weights learned from the outcomes it is graded on. | fixed after verification (bee5a67) |
| blind_leakage-6 | minor | lastcall loads the weeks 3-6 calibration even when the run turned calibration off, because load_calibration(None) falls back to the default file. A last call on a replay therefore re-applies in-sample calibration. | fixed, confirmed by the verifier |
| blind_leakage-7 | minor | BLOCKED_HOSTS is meant to drop 'DFS sites, paywalled or no-automation sites', but it misses DFS operators and projection or odds sites. Facts from them reach the forecaster. | fixed, confirmed by the verifier |

## Agent stages and decisions

| id | severity | finding | outcome |
|---|---|---|---|
| agents-1 | critical | fact_about matches facts about a different player, so 'not playing' removals, cuts and checker fails go through on a teammate's or another team's fact. Three causes: (a) a DST matches any fact carrying its team; (b) a shared last name on the same team matches; (c) the last name merely appearing in a same-team fact's text matches. A fourth, (d), needs no team at all: the substring test 'len(subj) >= 5 and (name in subj or subj in name)'. | fixed, confirmed by the verifier |
| agents-2 | major | OUT_WORDS treats any standalone 'out' as a hard out, including negated or past phrasing. remove_not_playing (line 242) and _hard_out (line 224) then drop players who are confirmed to play, and the hard-out path skips the novelty rule. | fixed after verification (bee5a67) |
| agents-3 | major | _verified accepts any same-team role, depth_chart or coaching fact as backing for a cut of any teammate, even when the fact's subject is a different named player. The same gap lets checker role blockers and backup multipliers through. | fixed, confirmed by the verifier |
| agents-4 | critical | The blinded forecaster receives the research agent's free-text game summary unfiltered. The research agent is given the betting market, so lines and salaries reach the forecaster. The blind filters only fact.text (pipeline.py:281, lastcall.py:90); the summary, subject and source_url pass through facts_text. | fixed, confirmed by the verifier |
| agents-5 | critical | When the checker still fails after 2 revisions, its fact-backed failures from attempt 2 are never added to `excluded` before the backup lineup is solved. A player the code itself verified as out can end up in the final lineup. | fixed, confirmed by the verifier |
| agents-6 | critical | The backup's `remove` list excludes players with no fact citation at all (the schema has no fact_ids for it). This breaks the rule that cuts are made only by citing facts, and it is applied whenever the main review fails. | fixed, confirmed by the verifier |
| agents-7 | major | The novelty rule's `priced` set holds every blinded fact of every game, including games whose forecast task failed or timed out and players who were never forecast (outside the pruned pool). Legitimate cuts are rejected as 'already priced in' when the forecaster never priced them. | fixed after verification (bee5a67) |
| agents-8 | major | Research tasks (WebSearch) are cacheable, and the cache key has no time component. A later re-run of the same contest file returns the earlier run's research verbatim with zero searches, so the news is stale. | fixed, confirmed by the verifier |
| agents-9 | major | The 60-minute rule is not enforced after research. The strategist, backup and checker use a fixed timeout_s=900 (lines 196, 269), Runner retries a timed-out task once (backend.py:193-203), and the Decider never sees the clock. | fixed, confirmed by the verifier |
| agents-10 | major | Learning compares the forecaster's median ('likely', which is also conditional on playing) with the baseline's mean. Fantasy points are right-skewed, so the forecast error is biased upward and the learned forecast weight is pushed toward the baseline. | fixed, confirmed by the verifier |
| agents-11 | major | grade_run adds the run's errors to the cumulative blend statistics on every call (update defaults to True). Grading the same run twice counts its games twice. | fixed, confirmed by the verifier |
| agents-12 | major | LEARNED = Path('data/learned') is relative to the working directory, while the pipeline reads blend_mod.LEARNED_DIR (an absolute, package-relative path). Grading from any other directory writes the learned weights where nothing reads them. | fixed, confirmed by the verifier |
| agents-13 | major | facts_text truncates at 40,000 characters, and the strategist (line 193) and checker (line 268) get every game's facts in one block. On big slates the last games' facts vanish, so picks from those games can never be cut or attacked with a fact. | fixed, confirmed by the verifier |
| agents-14 | minor | The run budget is checked only when a task starts, and spend is added only when it finishes, so concurrent tasks overshoot by up to concurrency x task cost. Tasks that time out are recorded at $0 (line 200), even though the SDK call was billed. No per-task max_budget_usd is set. | fixed after verification (bee5a67) |
| agents-15 | minor | Schema-invalid agent output crashes the decision instead of falling back. apply_strategist, checker_verdict and apply_backup assume well-formed lists and numbers. The backup is parsed at line 153 even when the main review succeeded, so a bad backup output kills a good run. | fixed, confirmed by the verifier |
| agents-16 | minor | A truncated or corrupt cache file raises JSONDecodeError out of Runner.run (outside any try), crashing run_all and the whole pipeline on every later run. The cache write at line 207 is not atomic. | fixed, confirmed by the verifier |
| agents-17 | minor | ruled_out_keys does exact normalised-name matching. Research entries such as 'Josh Allen (concussion)' or 'Khalil Shakir - WR' (the schema gives no format) match nobody, so research-confirmed outs stay in projections. | fixed after verification (bee5a67) |
| agents-18 | minor | Only decision.final passes the independent check_legal (decide.py:171). The per-entry lineups (pipeline.py:305) and the rules lineup (pipeline.py:310) are written unchecked. The upload writer validates only slot order and locked cells, not cap, positions, team or game rules, or duplicates. | fixed, confirmed by the verifier |
| agents-19 | minor | kickoff_windows schedules last-call runs from the main lineup's picks only. lastcall now checks every entry, but windows that hold picks only in other entries are never suggested. | fixed, confirmed by the verifier |
| agents-20 | minor | used_backup is set True and report.md says 'the backup review's lineup was used' even when the backup also failed. In that case bpoints=means and bremoved is empty, so the lineup is the plain solver lineup. | fixed, confirmed by the verifier |
| agents-21 | minor | fact_about and _verified compare raw team strings. Structured nflverse facts use nflverse codes ('LA') while FD contests use 'LAR', so every team-based branch (DST, last name plus team, team-level role facts) fails for those facts. | fixed, confirmed by the verifier |

## Compliance, safety and operations

| id | severity | finding | outcome |
|---|---|---|---|
| compliance_ops-1 | major | The research agent's free-text ruled_out list removes players from the whole run without citing any fact, which also opens the door to prompt injection from web text. STILL REPRODUCES in the working tree (pipeline.py:279, agents.py ruled_out_keys:320-327, apply_strategist:345). | fixed, confirmed by the verifier |
| compliance_ops-2 | major | Last-call news is never checked against the blocked-host policy, so an update sourced from FanDuel or DraftKings can mark a pick out and swap the lineup. STILL REPRODUCES in the working tree (parse_news:451-462 has no host_blocked call). | fixed, confirmed by the verifier |
| compliance_ops-3 | major | When every last-call news task fails (auth expired, budget, SDK error), lastcall and the CLI just report 'unchanged: no change' and never say the inactives check did not run. STILL REPRODUCES in the working tree. | fixed, confirmed by the verifier |
| compliance_ops-4 | major | The run budget is not the hard cap the CLI advertises: tasks running side by side all pass the check before any cost is recorded, no per-call max_budget_usd is ever set, and the cost of timed-out calls is never counted. PARTLY MITIGATED in the working tree (reservation plus a per-task cap), but timeouts are still counted at $0 and the per-task caps can add up to more than the budget. | fixed after verification (bee5a67) |
| compliance_ops-5 | major | The run deadline (the user's 'whole run < 60 min') is not enforced. Research and forecast timeouts have a 120 s floor, timeouts apply per attempt per wave inside the concurrency semaphore, and on HEAD the review stages get a fixed 900 s each with a retry. PARTLY MITIGATED in the working tree, which still overran a 60 s deadline by 2x. | fixed, confirmed by the verifier |
| compliance_ops-6 | major | The contest kind (cash or tournament) cannot be set. The CLI has no --kind flag, salary files always parse as GPP, and the SKILL tells Claude to ask the user for the kind it has no way to pass on. STILL REPRODUCES in the working tree. | fixed, confirmed by the verifier |
| compliance_ops-7 | minor | `dfs run` on the repo's own fixtures crashes with a raw ValueError traceback: the NFL provider can't find the synthetic games, the CLI catches only four exception types, and there is no flag to force the generic provider. STILL REPRODUCES in the working tree. | fixed, confirmed by the verifier |
| compliance_ops-8 | minor | package-data omits dfs/rules/correlations.yaml, and sim.load_correlations silently returns {} when the file is missing, so a non-editable install simulates with no stacking correlations. | fixed, confirmed by the verifier |
| compliance_ops-9 | minor | A trailing-dot hostname (draftkings.com.) slips past BLOCKED_HOSTS, both in the DFS_FETCH_ALLOW filter and in host_blocked(). The `dkapi` alternative can never match. STILL REPRODUCES in the working tree. | fixed, confirmed by the verifier |
| compliance_ops-10 | critical | Leaked blind: only the dossier's facts are filtered. The research summary goes to the forecaster untouched (facts_text prints '## game: {summary}'), and summaries from real research repeat betting lines, because the research prompt itself is given the market. FIXED in the working tree (blinded_facts_text drops the summary, and the market is removed from RESEARCH_PROMPT), not yet committed. | fixed, confirmed by the verifier |
| compliance_ops-11 | major | blind_text's leak regexes miss common ways of stating lines, salaries and fantasy scoring, so such facts reach the blinded forecaster. FIXED for the probed phrases in the working tree's rewritten _LEAKS (uncommitted). | fixed after verification (bee5a67) |
| compliance_ops-12 | major | OUT_WORDS matches a bare 'out', so strategist remove_not_playing and checker 'not_playing' blockers are accepted on facts saying the player WILL play, which also bypasses the priced-in novelty rule. FIXED in the working tree (says_out() with negation handling), not yet committed. | fixed after verification (bee5a67) |
| compliance_ops-13 | major | On the backup path, the backup agent's 'remove' list is applied with no fact citation at all. FIXED in the working tree (schema now needs fact_ids, plus a says_out check), not yet committed. | fixed, confirmed by the verifier |
| compliance_ops-14 | major | Grading the same run twice counts its errors twice in the learned blend weights. This can flip a position from the prior to a learned weight. FIXED in the working tree (a _graded_runs guard), not yet committed. | fixed, confirmed by the verifier |
| compliance_ops-15 | minor | LEARNED = Path('data/learned') is relative to the current directory, while the pipeline reads the absolute blend.LEARNED_DIR. Running `dfs grade` from any other directory writes the learned weights where nothing reads them. FIXED in the working tree (imports LEARNED_DIR), not yet committed. | fixed, confirmed by the verifier |
| compliance_ops-16 | minor | The LLM cache key carries no time, so re-running the same contest file days later silently reuses the old research, strategist and checker answers. FIXED in the working tree for research ('As of' hour in the prompt), not yet committed. | fixed, confirmed by the verifier |
| compliance_ops-17 | minor | A truncated cache file (the process killed mid-write, the write not being atomic at line 208) raises JSONDecodeError on every later run with the same prompt. FIXED in the working tree (corrupt cache counts as a miss, atomic replace), not yet committed. | fixed, confirmed by the verifier |
