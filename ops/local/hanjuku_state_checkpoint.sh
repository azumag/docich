#!/bin/bash
# Checkpoint the live Hanjuku run's savestate, tagged with the captured-castle
# count so a collapse can be rewound to the strongest recorded point.
#
# Usage: hanjuku_state_checkpoint.sh <runtime-dir> <dest-dir> [keep]
#   The bot's policy lives in <runtime-dir>/hanjuku_bot.json and the driver's
#   state in the HANJUKU_STATE path (passed as the first file of the runtime's
#   sibling 'states' dir by the caller); this script only needs the state file
#   path and the policy file path.
#
# The live runner (round 47) lost 6 castles between two 10-minute checkpoints
# and the newest checkpoint was already mid-collapse; tagging each copy with
# `captured` lets the operator pick the strongest one instead of the newest.
set -u
STATE=${1:?state file}
POLICY=${2:?hanjuku_bot.json}
DEST=${3:?dest dir}
KEEP=${4:-12}
mkdir -p "$DEST"
[ -f "$STATE" ] || exit 0
CAP=$(python3 - "$POLICY" <<'PY' 2>/dev/null || echo 0
import json, sys
try:
    policy = json.load(open(sys.argv[1])).get('policy') or {}
    print(len(policy.get('captured') or []))
except Exception:
    print(0)
PY
)
cp "$STATE" "$DEST/ckpt-c${CAP}-$(date +%H%M%S).state" || exit 0
# Keep the strongest by captured, then the newest as a tie-break.
ls -1 "$DEST"/ckpt-c*.state 2>/dev/null \
  | sed -E 's#.*/ckpt-c([0-9]+)-([0-9]+)\.state#\1 \2 &#' \
  | sort -k1,1nr -k2,2nr \
  | awk -v keep="$KEEP" 'NR > keep {print $3}' \
  | xargs -r rm -f
