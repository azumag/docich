#!/usr/bin/env bash
set -euo pipefail

root="/home/ubuntu/soren"
if [[ "${1:-}" == "--root" ]]; then
  [[ $# -eq 2 ]]
  root="$2"
  shift 2
fi
[[ $# -eq 0 ]]

pid_file="$root/tmp/state/direct_stream.pid"

read_pid() {
  local pid=""
  [[ -r "$pid_file" ]] || return 1
  pid="$(tr -d '[:space:]' < "$pid_file")"
  [[ "$pid" =~ ^[1-9][0-9]*$ ]] || return 1
  printf '%s\n' "$pid"
}

is_direct_stream() {
  local pid="$1" cmdline=""
  kill -0 "$pid" 2>/dev/null || return 1
  [[ -r "/proc/$pid/cmdline" ]] || return 1
  cmdline="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
  [[ "$cmdline" == *"lib/direct_stream.py run"* ]]
}

old_pid="$(read_pid)"
is_direct_stream "$old_pid"

# This one-shot restart is intentionally scoped to the direct-stream runner.
# SIGTERM lets direct_stream.py close FFmpeg/RTMP cleanly; start_all.sh remains
# alive and is the only component allowed to spawn the replacement process.
kill -TERM "$old_pid"

for _ in $(seq 1 160); do
  sleep 0.25
  new_pid="$(read_pid 2>/dev/null || true)"
  if [[ -n "$new_pid" && "$new_pid" != "$old_pid" ]] && is_direct_stream "$new_pid"; then
    # Require the reviewed overlay template auto-refresh implementation to be
    # present in the projected runtime before accepting the replacement.
    grep -q 'INLINE_BROADCAST_REFRESH_MS' "$root/lib/direct_overlay.mjs"
    grep -q 'GAME + OPS' "$root/overlays/direct_broadcast_overlay.html"
    exit 0
  fi
done

exit 1
