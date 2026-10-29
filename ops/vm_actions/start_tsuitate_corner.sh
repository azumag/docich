#!/usr/bin/env bash
set -euo pipefail

# Fixed public-repository operator action. No command, game, unit or path input.
# The owner workflow verifies protected main and the exact deployed SHA first.
if (( $# != 0 )); then
  printf '%s\n' 'start-tsuitate accepts no arguments' >&2
  exit 64
fi
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
DOCICH_TSUITATE_ROOT=/home/ubuntu/docich
DOCICH_TSUITATE_PYTHON="$DOCICH_TSUITATE_ROOT/.venv-trading/bin/python3"
if [[ ! -x "$DOCICH_TSUITATE_PYTHON" ]]; then
  DOCICH_TSUITATE_PYTHON=/usr/bin/python3
fi
(
  cd "$DOCICH_TSUITATE_ROOT"
  PYTHONPATH="$DOCICH_TSUITATE_ROOT/src" \
    "$DOCICH_TSUITATE_PYTHON" -m docich.tsuitate_start >/dev/null
)
printf '%s\n' 'queued Tsuitate through common corner coordinator'
