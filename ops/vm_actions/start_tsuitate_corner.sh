#!/usr/bin/env bash
set -euo pipefail

# Fixed owner action. This intentionally does NOT touch CornerRotation/GameSwitch.
# It starts one BetaArena match directly through the authenticated beta-control
# capability. The Python entrypoint uses a deterministic hourly run id, so a
# retry in the same hour cannot create a second game.
if (( $# != 0 )); then
  printf '%s\n' 'start-tsuitate accepts no arguments' >&2
  exit 64
fi
DOCICH_TSUITATE_ROOT=/home/ubuntu/docich
DOCICH_TSUITATE_PYTHON="$DOCICH_TSUITATE_ROOT/.venv-trading/bin/python3"
if [[ ! -x "$DOCICH_TSUITATE_PYTHON" ]]; then
  DOCICH_TSUITATE_PYTHON=/usr/bin/python3
fi
(
  cd "$DOCICH_TSUITATE_ROOT"
  PYTHONPATH="$DOCICH_TSUITATE_ROOT/src" \
    "$DOCICH_TSUITATE_PYTHON" -m docich.tsuitate_background_start >/dev/null
)
printf '%s\n' 'started Tsuitate beta match in background'
