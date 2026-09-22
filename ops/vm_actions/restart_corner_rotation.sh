#!/usr/bin/env bash
set -euo pipefail

# Canonical operator surface for the common rolling rotation. The service
# unit is resolved from the reviewed production unit files: the canonical
# name once migrated, the legacy name while the compatibility alias is not
# installed yet. Ambiguous or missing states fail closed. This is the
# complete operator surface: install the independent FIFO watchdog and
# replace only that corner's user service so the next tick imports the
# current queue implementation. Never restart the shared
# Soren/display/audio/stream units, and never accept an arbitrary unit name
# or command.

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
DOCICH_PROD_ROOT="${DOCICH_PROD_ROOT:-/home/ubuntu/docich}"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
canonical="docich-corner-rotation.service"
legacy="docich-retro-corner.service"
fifo_timer="docich-game-switch-fifo.timer"

TEMP_PATH=""
cleanup() {
  if [[ -n "$TEMP_PATH" ]]; then rm -f "$TEMP_PATH"; fi
}
trap cleanup EXIT

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
  fail "ambiguous corner rotation unit state; refusing to restart" 23
fi
if (( canonical_regular )); then
  unit="$canonical"
elif (( legacy_regular )); then
  unit="$legacy"
else
  fail "no corner rotation service unit found; refusing to restart" 24
fi

for name in docich-game-switch-fifo.service docich-game-switch-fifo.timer; do
  src="$DOCICH_PROD_ROOT/scripts/systemd/$name"
  [[ -f "$src" ]] || fail "missing FIFO watchdog unit template: $src" 21
  dest="$unit_dir/$name"
  [[ -L "$dest" ]] && fail "refusing to write a unit through a symlink: $dest" 22
  TEMP_PATH="$(mktemp "$unit_dir/.corner-rotation.XXXXXX")" || fail "cannot create a temporary unit file" 40
  sed "s#__DOCICH_ROOT__#$DOCICH_PROD_ROOT#g" "$src" > "$TEMP_PATH"
  chmod 0644 "$TEMP_PATH"
  mv -f "$TEMP_PATH" "$dest"
  TEMP_PATH=""
done
systemctl --user daemon-reload
systemctl --user enable --now "$fifo_timer"

systemctl --user show "$unit" >/dev/null
systemctl --user --no-block restart "$unit"
printf 'requested restart: %s\n' "$unit"
printf 'enabled FIFO watchdog: %s\n' "$fifo_timer"
