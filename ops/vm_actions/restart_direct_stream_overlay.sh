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
status_file="$root/tmp/state/direct_stream/status.json"

backend="$(sed -n 's/^SOREN_STREAM_BACKEND=//p' "$root/.env" 2>/dev/null | tail -1 | tr -d '"' | tr -d "'")"
if [[ "$backend" != "ffmpeg" ]]; then
  # No live direct-stream process is expected on OBS/fullscreen profiles.
  exit 0
fi

# The encoder runner is either the legacy `lib/direct_stream.py run` (pid file
# written by the start_all.sh supervisor) or, when docich owns the encoder
# (ADR 0007), `python3 -m docich.stream_supervisor [--root ROOT] run`, which has
# no pid file: its pid is the one recorded in the runner status file.
read_pid() {
  local pid=""
  if [[ -r "$pid_file" ]]; then
    pid="$(tr -d '[:space:]' < "$pid_file")"
    # A stale pid file (left by the legacy supervisor after a handover) must not
    # hide the live docich runner: only a live runner's pid is taken from it.
    if [[ "$pid" =~ ^[1-9][0-9]*$ ]] && is_direct_stream "$pid"; then
      printf '%s\n' "$pid"
      return 0
    fi
  fi
  read_status_pid
}

read_status_pid() {
  local pid=""
  [[ -r "$status_file" ]] || return 1
  pid="$(python3 - "$status_file" <<'PY' 2>/dev/null || true
import json
import sys

try:
    pid = json.load(open(sys.argv[1])).get("pid")
except Exception:
    pid = None
if type(pid) is int and pid > 1:
    print(pid)
PY
)"
  [[ "$pid" =~ ^[1-9][0-9]*$ ]] || return 1
  printf '%s\n' "$pid"
}

# Prints "legacy" or "docich" for a live runner of the exact expected shape.
runner_kind() {
  local pid="$1" cmdline=""
  kill -0 "$pid" 2>/dev/null || return 1
  [[ -r "/proc/$pid/cmdline" ]] || return 1
  cmdline="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
  if [[ "$cmdline" == *"lib/direct_stream.py run"* ]]; then
    printf 'legacy\n'
    return 0
  fi
  if [[ "$cmdline" =~ ^([^\ ]*/)?python3(\.[0-9]+)?\ -m\ docich\.stream_supervisor\ (--root\ ([^\ ]+)\ )?run\ $ ]] \
    && { [[ -z "${BASH_REMATCH[3]}" ]] || [[ "${BASH_REMATCH[4]}" == "$root" ]]; }; then
    printf 'docich\n'
    return 0
  fi
  return 1
}

is_direct_stream() {
  runner_kind "$1" >/dev/null
}

old_pid="$(read_pid)"
old_kind="$(runner_kind "$old_pid")"

# This one-shot restart is intentionally scoped to the encoder runner.
# SIGTERM lets the runner close FFmpeg/RTMP cleanly; its supervisor (start_all.sh
# for the legacy runner, docich-stream.service with Restart=always for docich) is
# the only component allowed to spawn the replacement process.
kill -TERM "$old_pid"

# A docich runner's graceful stop (q, then SIGINT) can take 30 s before the unit
# restarts it, so it gets a longer window than the legacy runner.
attempts=160
[[ "$old_kind" == "docich" ]] && attempts=360

for _ in $(seq 1 "$attempts"); do
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
