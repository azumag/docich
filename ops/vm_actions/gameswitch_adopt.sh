#!/usr/bin/env bash
set -euo pipefail

# Fixed owner-only GameSwitch failed-candidate adoption surface (#2088). Runs
# only the reviewed helper: it double-keys the declared phase and
# cleanup_pending against the canonical GameSwitch state in fixed form, proves
# the still-running failed candidate from the immutable rollback-failure
# receipt and the seated runtime directories, then runs the canonical CLI
# ``recover --adopt-failed-candidate`` once with a bounded deadline and
# verifies the same runtime actually became active with cleanup settled. It
# never restarts shared display/audio/stream units, never signals an actor
# directly, never edits tracked files, and accepts no extra arguments.

[[ "$#" -eq 0 ]] || exit 2
[[ "$(pwd -P)" == /home/ubuntu/docich ]] || exit 3

GS_ADOPT_PHASE="${GS_ADOPT_PHASE:-}"
GS_ADOPT_CLEANUP_PENDING="${GS_ADOPT_CLEANUP_PENDING:-}"
case "$GS_ADOPT_PHASE" in failed) ;; *) exit 64 ;; esac
case "$GS_ADOPT_CLEANUP_PENDING" in true) ;; *) exit 64 ;; esac
export GS_ADOPT_PHASE GS_ADOPT_CLEANUP_PENDING

exec timeout --kill-after=5s 480s python3 ops/vm_actions/gameswitch_adopt.py \
  "$GS_ADOPT_PHASE" "$GS_ADOPT_CLEANUP_PENDING"
