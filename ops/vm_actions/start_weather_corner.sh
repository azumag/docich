#!/usr/bin/env bash
set -euo pipefail

# Fixed public-repository operator action. No command, game, unit or path input.
# The owner workflow verifies protected main and the exact deployed SHA first.
if (( $# != 0 )); then
  printf '%s\n' 'start-weather accepts no arguments' >&2
  exit 64
fi
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
DOCICH_WEATHER_ROOT=/home/ubuntu/docich
DOCICH_WEATHER_PYTHON="$DOCICH_WEATHER_ROOT/.venv-trading/bin/python3"
if [[ ! -x "$DOCICH_WEATHER_PYTHON" ]]; then
  DOCICH_WEATHER_PYTHON=/usr/bin/python3
fi
(
  cd "$DOCICH_WEATHER_ROOT"
  PYTHONPATH="$DOCICH_WEATHER_ROOT/src" \
    "$DOCICH_WEATHER_PYTHON" -m docich.weather_start >/dev/null
)
printf '%s\n' 'queued Weather through common corner coordinator'
