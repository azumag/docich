#!/usr/bin/env bash
set -euo pipefail

# Production default is fixed. --root exists only so the helper can be exercised
# by repository tests without touching /home/ubuntu/soren.
root="/home/ubuntu/soren"
if [[ "${1:-}" == "--root" ]]; then
  [[ $# -eq 2 ]]
  root="$2"
  shift 2
fi
[[ $# -eq 0 ]]

pid_file="$root/tmp/state/radio_worker.pid"

read_pid() {
  local pid=""
  [[ -r "$pid_file" ]] || return 1
  pid="$(tr -d '[:space:]' < "$pid_file")"
  [[ "$pid" =~ ^[1-9][0-9]*$ ]] || return 1
  printf '%s\n' "$pid"
}

is_radio_worker() {
  local pid="$1" cmdline=""
  kill -0 "$pid" 2>/dev/null || return 1
  [[ -r "/proc/$pid/cmdline" ]] || return 1
  cmdline="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
  [[ "$cmdline" == *"workers/radio_worker.sh"* ]]
}

old_pid="$(read_pid)"
is_radio_worker "$old_pid"

# TERM is intentional rather than HUP: a projected workers/radio_worker.sh
# cannot replace functions already resident in the old bash process. The
# existing supervisor owns restart and launches the reviewed projected script.
kill -TERM "$old_pid"

# Fail closed unless the supervisor replaces the worker with a new live PID
# whose command line is still the reviewed radio worker.
for _ in $(seq 1 80); do
  sleep 0.25
  new_pid="$(read_pid 2>/dev/null || true)"
  if [[ -n "$new_pid" && "$new_pid" != "$old_pid" ]] && is_radio_worker "$new_pid"; then
    exit 0
  fi
done

exit 1
