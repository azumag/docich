#!/usr/bin/env bash
set -euo pipefail

# Install and enable only the rolling-corner timer. This is intentionally
# separate from the shared display/Soren/audio services and never starts a
# game directly; the first bounded tick is scheduled by systemd itself.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
DOCICH_PROD_ROOT="${DOCICH_PROD_ROOT:-/home/ubuntu/docich}"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
timer="docich-retro-corner.timer"

mkdir -p "$unit_dir"
for name in docich-retro-corner.service docich-retro-corner.timer; do
  src="$DOCICH_PROD_ROOT/scripts/systemd/$name"
  [[ -f "$src" ]] || { echo "missing corner rotation unit template: $src" >&2; exit 21; }
  sed "s#__DOCICH_ROOT__#$DOCICH_PROD_ROOT#g" "$src" > "$unit_dir/$name"
done

systemctl --user daemon-reload
systemctl --user enable --now "$timer"
systemctl --user is-enabled --quiet "$timer"
systemctl --user is-active --quiet "$timer"
printf 'enabled rolling corner timer: %s\n' "$timer"
