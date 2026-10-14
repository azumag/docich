#!/usr/bin/env bash
set -euo pipefail

# Fixed public-repository operator action. No command, game, unit or path input.
# The owner workflow verifies protected main and the exact deployed SHA first.
if (( $# != 0 )); then
  printf '%s\n' 'start-hanjuku accepts no arguments' >&2
  exit 64
fi
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
DOCICH_HANJUKU_ROOT=/home/ubuntu/docich
DOCICH_HANJUKU_PYTHON="$DOCICH_HANJUKU_ROOT/.venv-trading/bin/python3"
if [[ ! -x "$DOCICH_HANJUKU_PYTHON" ]]; then
  DOCICH_HANJUKU_PYTHON=/usr/bin/python3
fi
# queue_manual() is a short durable write. Wait for its real exit status so
# the owner workflow cannot report success merely because a child was exec'd.
(
  cd "$DOCICH_HANJUKU_ROOT"
  PYTHONPATH="$DOCICH_HANJUKU_ROOT/src" \
    "$DOCICH_HANJUKU_PYTHON" -m docich.hanjuku_corner >/dev/null
)
printf '%s\n' 'queued Hanjuku through common corner coordinator'
