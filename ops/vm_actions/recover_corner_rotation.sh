#!/usr/bin/env bash
set -euo pipefail

# Canonical fixed recovery surface for a corner slot whose canonical
# transition failed. The service unit is resolved from the reviewed
# production unit files: the canonical name once migrated, the legacy name
# while the compatibility alias is not installed yet. Ambiguous or missing
# states fail closed.
#
# Two phase-gated steps run in order (#986):
#   1. `corner-rotation recover` resolves the durable rotation latch. It never
#      edits the ledger by hand: a reservation that already ended is committed
#      from its own adapter observation, one that never started is handed back
#      to the normal tick path, and a still-running/corner-level failure stays
#      latched and exits non-zero (nothing below runs in that case).
#   2. the reviewed service unit is restarted so the very next tick retries the
#      same recorded game through the normal game-switch/program-slot gates.
# It never resets a live boundary and never restarts shared Soren/display/
# audio/stream services, and never accepts an arbitrary unit name or command.

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
DOCICH_PROD_ROOT="${DOCICH_PROD_ROOT:-/home/ubuntu/docich}"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
canonical="docich-corner-rotation.service"
legacy="docich-retro-corner.service"
launcher="$DOCICH_PROD_ROOT/bin/docich"
config="$DOCICH_PROD_ROOT/config/docich.soren-live.toml"

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

if [[ ! -x "$launcher" || ! -f "$config" ]]; then
  fail "reviewed docich launcher or config missing; refusing to recover" 25
fi

# Fail-closed latch resolution: a refusal stops here without touching the unit.
"$launcher" --config "$config" corner-rotation recover

systemctl --user --no-block restart "$unit"
printf 'requested failed-slot recovery: %s\n' "$unit"
