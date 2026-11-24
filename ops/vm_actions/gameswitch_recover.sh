#!/usr/bin/env bash
set -euo pipefail

# Fixed owner-only GameSwitch recover surface (#1960). Runs only the
# reviewed helper: it double-keys the declared phase and cleanup_pending
# against the canonical GameSwitch state in fixed form, refuses unknown
# drift or missing ownership proof, then runs the canonical CLI recover
# once with a bounded deadline. It never restarts shared display/audio/
# stream units, never signals an actor directly, never edits tracked
# files, and accepts no extra arguments.

[[ "$#" -eq 0 ]] || exit 2
[[ "$(pwd -P)" == /home/ubuntu/docich ]] || exit 3

GS_RECOVER_PHASE="${GS_RECOVER_PHASE:-}"
GS_RECOVER_CLEANUP_PENDING="${GS_RECOVER_CLEANUP_PENDING:-}"
case "$GS_RECOVER_PHASE" in failed|recovery_required) ;; *) exit 64 ;; esac
case "$GS_RECOVER_CLEANUP_PENDING" in true|false) ;; *) exit 64 ;; esac
export GS_RECOVER_PHASE GS_RECOVER_CLEANUP_PENDING

exec timeout --kill-after=5s 480s python3 ops/vm_actions/gameswitch_recover.py \
  "$GS_RECOVER_PHASE" "$GS_RECOVER_CLEANUP_PENDING"
