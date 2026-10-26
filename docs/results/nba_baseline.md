# NBA baseline: tuning and checks (2026-10-23)

The NBA provider (`dfs/projections/nba_provider.py`) projects DraftKings points as minutes x
points per minute, from ESPN player box scores in the sportsdataverse release files. This page
records how its numbers were chosen.

## Backtest

- **Setting:** the start of a season, like the 2026-10-23 slate (each team's 2nd game).
- **Target games:** 2025-26 regular-season games from opening night through 21 days later.
- **History:** all 2024-25 games plus 2025-26 games strictly before the target date. Nothing after
  a target game is read.
- **Scoring:** rows where the player played. Availability is handled separately, below.
- **Rows:** 3,593 of 4,310 target rows were played. 3,202 of those have 2024-25 history, and those
  are the headline numbers.
- **Who ran it:** two independent model studies, plus a judge that re-scored both on the same rows.
- **Scripts:** in the session scratch folder (not kept in the repo).

| Model | RMSE, all | RMSE, projection 15+ | Bias |
|---|---:|---:|---:|
| Last season's DK points per game (naive) | 11.05 | 11.54 | |
| **A: pseudo-count blend (chosen)** | **9.91** | **10.56** | +0.2 |
| B: exponentially weighted game logs | 9.93 | 10.62 | +0.5 |

**At this point in the season (1–2 games played, 861 rows):** A scores 9.84 and B 10.09.

**Out of sample (2026-27 opening games, 327 rows):** A 9.93, B 10.52. Projections ran 3.3 points
high because players got fewer minutes than last season. The model therefore scales last
season's minutes by 0.97.

## Chosen model (A)

- **Minutes** = (this season's minutes + k x last season's minutes per game x 0.97) / (games + k).
  k = 0.8 for a player on the same team, 0.5 for one on a new team.
- **Rate** = (this season's DK points + r x prior rate) / (this season's minutes + r).
  r = 250 on the same team, 75 on a new team.
  - The prior rate is last season's DK points per minute, with 50 pseudo-minutes of the
    position-group league rate (G 0.95, F 0.96–0.97, C 1.11).
- **Rookies with games this season:** shrink toward 10 minutes (k = 0.25) and 90% of the league
  rate (r = 100).
- **Players with no NBA game on record:** 10 minutes x 90% of the league rate.
- **Long injury (added by hand, not tuned):** a last season under 20 games adds the season before
  it. Haliburton, for example, missed 2025-26.

The optimum is flat. k from 0.5 to 1.0 and r from 150 to 250 are all within 0.03 RMSE.

## Spread

- **Fit:** sd = 6.715 + 0.1365 x mean (given he plays), fitted on binned residuals.
- **Skew:** residuals are right-skewed. Their measured 10th and 90th percentiles are −1.13 and
  +1.33 sd. The provider uses a two-piece distribution with skew 0.2, which gives −1.17 / +1.36.

## Availability

All rates below come from the 2025-26 regular season.

| Last game | P(does not play next game) |
|---|---:|
| Played 30+ minutes | 0.02 |
| Played 20–30 | 0.04 |
| Played 10–20 | 0.095 |
| Played under 10 | 0.37 |
| Missing from the box score (injured, inactive) | 0.58–0.66 (0.52 after one game, rising with the streak) |

- **Missing from the last box score:** the provider uses `p_zero = 0.6`.
- **Started the last game but left early** (under half his usual minutes, usual 20+; usual is his
  minutes per game in the team's earlier games, last season's with fewer than 2): 2025-27, 286
  starters, 0.60 [0.55, 0.66] did not play the next game (0.49 absent from its box score). By
  share of usual: under 30% 0.81 (n 98), 30-40% 0.63 (n 63), 40-50% 0.43 (n 125). Starters at
  normal minutes: 0.07. The provider uses these three rates (`p_zero_left_early`) unless research
  finds a status, and the research desk is told to check the player by name. Blowouts are a fifth
  of the flags and still 0.54; 5+ fouls 8 flags, none missed; 28% played normal minutes next game.
  Bench players under half their minutes are not flagged (0.23-0.31, mostly coach's decisions).
- **Tonight's statuses:** research sets these from cited facts (`agents.apply_status`):
  doubtful 0.75, questionable 0.30, probable 0.05, active 0. Out and inactive players are
  removed.
- **Reason codes:** ESPN writes "COACH'S DECISION" on almost every row, played or not. The box
  score's reason field is therefore not used.

## Team minutes

Each team plays 240 minutes. Before reconciliation, tonight's teams summed to 247–284 expected
minutes:

- players who played last game summed to 222–235 (right);
- the excess came from players who missed the opener and rookies with no record.

After research, `reconcile_minutes` takes any expected minutes above 242 that uncertain players
add out of the regulars, in proportion, with a floor of 85% of their minutes. A confirmed return
therefore costs his teammates minutes, and a ruled-out player costs them nothing.

## Data

- **Source:** ESPN box scores via `sportsdataverse-data` release files (`espn_nba_player_boxscores`).
- **Terms:** CC-BY-4.0, attribute sportsdataverse. The rows are ESPN-derived, the same class of
  data accepted for college football on 2026-10-21. They are used for personal, non-commercial
  analysis and not redistributed.
- **Freshness:** the current season's file is refreshed when older than 6 hours.
- **ESPN servers:** never contacted.
