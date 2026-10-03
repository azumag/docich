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
[[ -f "$env_file" ]]
set -a
# shellcheck disable=SC1090
. "$env_file"
set +a

if [[ "${SOREN_STATUS_OVERLAY_WATCHERS_ENABLED:-0}" != "1" ]]; then
  exit 0
fi

declare -a names scripts outputs markers
if [[ "${SOREN_UNIFIED_OVERLAY_ENABLED:-0}" == "1" ]]; then
  names=("soren_overlay_watch")
  scripts=("generate_soren_overlay.sh")
  outputs=("tmp/state/soren_overlay.html")
  markers=("SOREN CONTROL DECK")
else
  names=("status_overlay_watch" "show_status_overlay_watch")
  scripts=("generate_status_overlay.sh" "generate_show_status_overlay.sh")
  outputs=("tmp/state/status_overlay.html" "tmp/state/show_status_overlay.html")
  markers=("GAME PERFORMANCE" "OPS HEALTH")
fi

read_pid() {
  local file="$1" pid=""
  [[ -r "$file" ]] || return 1
  pid="$(tr -d '[:space:]' < "$file")"
  [[ "$pid" =~ ^[1-9][0-9]*$ ]] || return 1
  printf '%s\n' "$pid"
}

matches_script() {
  local pid="$1" script="$2" cmdline=""
  kill -0 "$pid" 2>/dev/null || return 1
  [[ -r "/proc/$pid/cmdline" ]] || return 1
  cmdline="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
  [[ "$cmdline" == *"$script watch"* ]]
}

for i in "${!names[@]}"; do
  name="${names[$i]}"
  script="${scripts[$i]}"
  output="$root/${outputs[$i]}"
  marker="${markers[$i]}"
  pid_file="$root/tmp/state/$name.pid"

  old_pid="$(read_pid "$pid_file")"
  matches_script "$old_pid" "$script"

  # Only the reviewed overlay watch loop is replaced. start_all.sh remains the
  # sole owner of respawn and launches the newly deployed script.
  kill -TERM "$old_pid"

  new_pid=""
  for _ in $(seq 1 160); do
    sleep 0.25
    candidate="$(read_pid "$pid_file" 2>/dev/null || true)"
    if [[ -n "$candidate" && "$candidate" != "$old_pid" ]] && matches_script "$candidate" "$script"; then
      new_pid="$candidate"
      break
    fi
  done
  [[ -n "$new_pid" ]]

  # Do not accept process replacement alone: require one fresh render from the
  # new layout so OBS/browser sources cannot keep showing the old terminal UI.
  rendered=0
  for _ in $(seq 1 80); do
    sleep 0.25
    if [[ -r "$output" ]] && grep -Fq "$marker" "$output"; then
      rendered=1
      break
    fi
  done
  [[ "$rendered" -eq 1 ]]
done
