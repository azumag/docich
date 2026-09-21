#!/usr/bin/env bash
set -euo pipefail

# This is intentionally the complete operator surface: a code deployment can
# leave a pre-deploy retro-corner Python process waiting at a round boundary.
# Install the independent FIFO watchdog and replace only that corner's user
# service so the next tick imports the current queue implementation. Never
# restart the shared Soren/display/audio/stream units here.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
DOCICH_PROD_ROOT="${DOCICH_PROD_ROOT:-/home/ubuntu/docich}"
unit="docich-retro-corner.service"
fifo_timer="docich-game-switch-fifo.timer"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

mkdir -p "$unit_dir"
for name in docich-game-switch-fifo.service docich-game-switch-fifo.timer; do
  src="$DOCICH_PROD_ROOT/scripts/systemd/$name"
  [[ -f "$src" ]] || { echo "missing FIFO watchdog unit template: $src" >&2; exit 21; }
  sed "s#__DOCICH_ROOT__#$DOCICH_PROD_ROOT#g" "$src" > "$unit_dir/$name"
done
systemctl --user daemon-reload
systemctl --user enable --now "$fifo_timer"

systemctl --user show "$unit" >/dev/null
systemctl --user --no-block restart "$unit"
printf 'requested restart: %s\n' "$unit"
printf 'enabled FIFO watchdog: %s\n' "$fifo_timer"
