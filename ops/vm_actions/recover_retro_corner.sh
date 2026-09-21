#!/usr/bin/env bash
set -euo pipefail

# Fixed recovery surface for a retro slot whose canonical transition failed.
# The service tick performs the phase-gated recovery and retries the same
# recorded game. It never resets a live boundary and never restarts shared
# Soren/display/audio/stream services.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
DOCICH_PROD_ROOT="${DOCICH_PROD_ROOT:-/home/ubuntu/docich}"
unit="docich-retro-corner.service"

systemctl --user show "$unit" >/dev/null
systemctl --user --no-block restart "$unit"
printf 'requested failed-slot recovery: %s\n' "$unit"
