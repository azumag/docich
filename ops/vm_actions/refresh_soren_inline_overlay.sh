#!/usr/bin/env bash
set -euo pipefail

root="/home/ubuntu/soren"
if [[ "${1:-}" == "--root" ]]; then
  [[ $# -eq 2 ]]
  root="$2"
  shift 2
fi
[[ $# -eq 0 ]]

env_file="$root/.env"
tool="$root/tools/refresh_inline_broadcast_overlay.mjs"

[[ -f "$env_file" ]]
[[ -f "$tool" ]]

set -a
# shellcheck disable=SC1090
. "$env_file"
set +a

backend="${SOREN_STREAM_BACKEND:-obs}"
broadcast="${SOREN_DIRECT_BROADCAST_OVERLAY_ENABLED:-0}"
layout="${SOREN_DIRECT_STAGE_LAYOUT:-fullscreen}"

# Only the FFmpeg dashboard owns the inline GAME/OPS rails. Other profiles are
# intentionally untouched.
if [[ "$backend" != "ffmpeg" || "$broadcast" != "1" || "$layout" != "dashboard" ]]; then
  exit 0
fi

# The reviewed helper attaches to loopback CDP, replaces only an already-owned
# broadcast rail, never navigates or stops the game, and detaches immediately.
timeout 15 node "$tool" >/dev/null
