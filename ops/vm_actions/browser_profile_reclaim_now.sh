#!/usr/bin/env bash
# Fixed owner-only production helper for bounded browser-profile reclaim in
# completed runtime generations (azumag/docich#3122).
#
# Two-phase, owner-gated contract:
#   Phase 1 (default): dry-run only. Prints the fixed-enum JSON plan including
#     plan_sha256, entries_planned and bytes_planned. Nothing is deleted.
#   Phase 2 (--apply): requires EXPECT_PLAN=<plan_sha256 of the reviewed
#     dry-run>. Refuses to delete anything else (plan_mismatch).
#
# The actual mutation contract lives in the reviewed
# runtime_browser_profile_reclaim.py: completed-generation gates
# (RUN_ID / canonical inactive / terminal_identity), game-switch.lock shared
# flock, symlink/nlink/unsafe-entry rejection, per-entry re-stamp on apply.
# hanjuku logs, frames and the generation dir are never removed.
#
# No restart, sudo, arbitrary path, prompt, model output, or credential data
# is touched. Output is retained only in the VM-private exec log.
set -euo pipefail

root="/home/ubuntu/docich"
state_dir="$root/run-soren-live"
# Use the deployed checkout path rather than dirname "$0": vm-operations may
# execute this wrapper via stdin, where $0 does not identify this file.
helper="$root/ops/vm_actions/runtime_browser_profile_reclaim.py"

mode="dry-run"
case "${1:-}" in
  "") ;;                # Phase 1: dry-run plan only.
  --apply) mode="apply" ;;  # Phase 2: reviewed-digest apply.
  *) exit 2 ;;
esac

[[ -d "$state_dir" ]] || { echo "state dir missing" >&2; exit 2; }
[[ -f "$helper" && ! -L "$helper" ]] || { echo "reviewed helper missing" >&2; exit 2; }
[[ -f "$state_dir/game_switch.json" && ! -L "$state_dir/game_switch.json" ]] || exit 2
[[ -f "$state_dir/locks/game-switch.lock" && ! -L "$state_dir/locks/game-switch.lock" ]] || exit 2

if [[ "$mode" == "apply" ]]; then
  # Owner consent gate: the digest of the reviewed dry-run plan must be
  # supplied explicitly. The current reclaim size is determined by Phase 1;
  # later bounded runs reuse the same two-phase flow.
  [[ -n "${EXPECT_PLAN:-}" ]] || { echo "EXPECT_PLAN digest required for apply" >&2; exit 2; }
  exec python3 "$helper" "$state_dir" --apply --expect-plan "$EXPECT_PLAN" \
    --batch 100 --keep-recent 3 --min-age-hours 24
fi

exec python3 "$helper" "$state_dir" --batch 100 --keep-recent 3 --min-age-hours 24
