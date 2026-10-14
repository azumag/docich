#!/usr/bin/env bash
set -euo pipefail

# Fixed public-repository operator action for a dead Hanjuku runtime (Issue
# #1280). No command, game, unit, path or argument is accepted as input; the
# owner workflow verifies protected main and the exact deployed SHA first.
#
# The reviewed CLI recovery runs synchronously so the workflow gets its exit
# code: verify the recorded runtime identity against canonical, rebuild the
# shared session/display, re-run the runtime's own preflight /
# materialize_runtime / readiness contract, then end the corner through the
# explicit operator stop path (save attempt with a bounded forced unsaved
# stop). It never restarts the shared Soren/display/audio/stream services,
# never touches another corner, and never edits game-switch state by hand.
if (( $# != 0 )); then
  printf '%s\n' 'recover-runtime accepts no arguments' >&2
  exit 64
fi
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
DOCICH_PROD_ROOT="${DOCICH_PROD_ROOT:-/home/ubuntu/docich}"
launcher="$DOCICH_PROD_ROOT/bin/docich"
config="$DOCICH_PROD_ROOT/config/docich.soren-live.toml"
if [[ ! -x "$launcher" || ! -f "$config" ]]; then
  printf '%s\n' 'reviewed docich launcher or config missing; refusing to recover' >&2
  exit 25
fi
exec "$launcher" --config "$config" retro-corner recover-runtime
