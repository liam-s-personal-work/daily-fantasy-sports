#!/usr/bin/env bash
# Replays every real 2026 slate we have files for, one at a time (timings stay clean).
set -u
cd "$(dirname "$0")/.."
S=data/cache/real_samples
run() { echo "=== $1"; python3 scripts/replay.py "$S/$1" --season 2026 --week "$2" --now "$3" > "runs/replays/$4.log" 2>&1; tail -3 "runs/replays/$4.log"; }
run fd_classic_wk6_main_18Oct2026.csv 6 2026-10-18T16:00:00+00:00 fd_wk6
run dk_showdown_wk6_DAL_GB_18Oct2026.csv 6 2026-10-18T23:00:00+00:00 dk_sd_wk6
run fd_showdown_09Sep2026_ne_sea.csv 1 2026-09-09T22:00:00+00:00 fd_sg_wk1
run DKEntriesClassic.csv 3 2026-09-27T16:00:00+00:00 dk_wk3
