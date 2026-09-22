#!/usr/bin/env bash
set -euo pipefail

# Canonical fixed recovery surface for a corner slot whose canonical
# transition failed. The service unit is resolved from the reviewed
# production unit files: the canonical name once migrated, the legacy name
# while the compatibility alias is not installed yet. Ambiguous or missing
# states fail closed. The service tick performs the phase-gated recovery and
# retries the same recorded game. It never resets a live boundary and never
# restarts shared Soren/display/audio/stream services, and never accepts an
# arbitrary unit name or command.

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
DOCICH_PROD_ROOT="${DOCICH_PROD_ROOT:-/home/ubuntu/docich}"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
canonical="docich-corner-rotation.service"
legacy="docich-retro-corner.service"

fail() {
  printf '%s\n' "$1" >&2
  exit "${2:-1}"
}

mkdir -p "$unit_dir"

canonical_regular=0
legacy_regular=0
if [[ -f "$unit_dir/$canonical" && ! -L "$unit_dir/$canonical" ]]; then canonical_regular=1; fi
if [[ -f "$unit_dir/$legacy" && ! -L "$unit_dir/$legacy" ]]; then legacy_regular=1; fi
if (( canonical_regular && legacy_regular )); then
  fail "ambiguous corner rotation unit state; refusing to recover" 23
fi
if (( canonical_regular )); then
  unit="$canonical"
elif (( legacy_regular )); then
  unit="$legacy"
else
  fail "no corner rotation service unit found; refusing to recover" 24
fi

systemctl --user show "$unit" >/dev/null
systemctl --user --no-block restart "$unit"
printf 'requested failed-slot recovery: %s\n' "$unit"
