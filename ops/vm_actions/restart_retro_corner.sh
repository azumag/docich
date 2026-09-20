#!/usr/bin/env bash
set -euo pipefail

# This is intentionally the complete operator surface: a code deployment can
# leave a pre-deploy retro-corner Python process waiting at a round boundary.
# Replace only that corner's user service so the next tick imports the current
# queue implementation. Never restart the shared Soren/display/audio/stream
# units here.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
unit="docich-retro-corner.service"

systemctl --user show "$unit" >/dev/null
systemctl --user --no-block restart "$unit"
printf 'requested restart: %s\n' "$unit"
