#!/bin/sh
# Real-binary smoke for one retro CLI game, in a throwaway offline container.
#
#   scripts/retro_smoke/run.sh <game> [seconds]      # default 120s
#
# Runs the game's real wrapper + command brain through docich's own adapter and
# agent-loop iteration (see smoke_play.py) inside Docker, against a read-only
# COPY of this checkout.  --network none: nothing can reach production, and the
# wrappers' default production scorelog paths are overridden into $OUT.
#
# env: OUT (default: a fresh temp dir), SMOKE_NULL_BRAIN, SMOKE_INTERVAL_MS,
#      SMOKE_DUMP_ALL (see smoke_play.py), IMAGE (default docich-retro-smoke:local)
set -eu

GAME="${1:?usage: run.sh <game> [seconds]}"
SECS="${2:-120}"
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
IMAGE="${IMAGE:-docich-retro-smoke:local}"
OUT="${OUT:-$(mktemp -d "${TMPDIR:-/tmp}/retro-smoke.XXXXXX")}"
mkdir -p "$OUT"

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  docker build -q -t "$IMAGE" "$HERE" >/dev/null
fi

docker run --rm --network none --name "retro-smoke-$GAME-$$" \
  -v "$REPO":/src:ro \
  -v "$HERE/smoke_play.py":/smoke_play.py:ro \
  -v "$OUT":/out \
  -e SMOKE_NULL_BRAIN="${SMOKE_NULL_BRAIN:-0}" \
  -e SMOKE_INTERVAL_MS="${SMOKE_INTERVAL_MS:-}" \
  -e SMOKE_DUMP_ALL="${SMOKE_DUMP_ALL:-0}" \
  -e BASTET_SCORELOG="/out/$GAME/scores.jsonl" \
  -e MOONBUGGY_SCORELOG="/out/$GAME/scores.jsonl" \
  -e NINVADERS_SCORELOG="/out/$GAME/scores.jsonl" \
  -e NSNAKE_SCORELOG="/out/$GAME/scores.jsonl" \
  -e PACMAN_SCORELOG="/out/$GAME/scores.jsonl" \
  "$IMAGE" sh -c "
    mkdir -p /out/$GAME /work &&
    tar --exclude=.git -C /src -cf - . | tar -C /work -xf - &&
    cd /work && python3 /smoke_play.py '$GAME' '$SECS' 2>/out/$GAME/stderr.log"

echo "results: $OUT/$GAME (summary.json, snapshots.json, actions.jsonl, final_pane.txt, scores.jsonl)"
