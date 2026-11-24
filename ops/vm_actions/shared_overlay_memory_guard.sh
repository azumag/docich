#!/usr/bin/env bash
# docich#5112: reload the shared overlay browser only when its cgroup memory
# exceeds the limit, with a cooldown so a regrowing leak cannot restart it in a
# loop. Safe to call from a systemd timer; exits 0 for ok/cooldown/reloaded.
set -euo pipefail
exec python3 "$(dirname "$0")/shared_overlay_memory_guard.py" "$@"
