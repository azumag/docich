#!/usr/bin/env bash
set -euo pipefail

# Fixed owner-only operator action: clear a clock-gap quarantine hold for an
# already-queued Hanjuku manual request. It accepts no arguments. It does not
# start, stop or switch any game; the normal rotation tick processes the
# existing queue through the reservation/game-boundary gates.
if (( $# != 0 )); then
  printf '%s\n' 'clear-hanjuku-clock-quarantine accepts no arguments' >&2
  exit 64
fi
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
root="${DOCICH_PROD_ROOT:-/home/ubuntu/docich}"
config="$root/config/docich.soren-live.toml"
[[ -f "$root/src/docich/hanjuku_corner.py" && -f "$config" ]] || exit 25
python="$root/.venv-trading/bin/python3"
[[ -x "$python" ]] || python=/usr/bin/python3
export PYTHONPATH="$root/src"
exec "$python" -B -m docich.hanjuku_corner --clear-clock-quarantine
