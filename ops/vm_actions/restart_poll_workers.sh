#!/usr/bin/env bash
set -euo pipefail

# Reviewed, bounded targeted restart of the supervised polling workers.
#
# Purpose (azumag/docich#970): `audio_worker` / `youtube_worker` / `poll_worker` /
# `prediction_worker` / `stream_noon_audit` are long-lived bash processes started
# once by `start_all.sh`. A projected Soren runtime change is therefore NOT
# active in production until those processes are replaced: `start_all.sh` only
# respawns a worker that has exited, and it never restarts a worker because its
# file changed. The overlay / radio / BGM / webui paths already have fixed
# reviewed restart hooks; the polling workers did not, so activating a reviewed
# update needed an ad-hoc SSH session.
#
# This helper is the fixed, reviewed path for exactly these five workers and
# nothing else. It is invoked from the push-deploy workflow only when the
# reviewed `ops/vm_actions/restart_poll_workers_epoch` changes, so a restart is
# an explicit, reviewable decision rather than a side effect of every deploy.
#
# Safety properties:
#   - No root privileges and no caller input. The production root is fixed;
#     `--root` exists only so repository tests can exercise the helper without
#     touching /home/ubuntu/soren.
#   - Only the five named polling workers are signalled. The display, Soren,
#     audio server, radio, game, stream encoder and Twitch units are never
#     touched.
#   - A PID recorded in `tmp/state/<worker>.pid` is signalled only when it is
#     alive and its `/proc/<pid>/cmdline` still contains `workers/<worker>.sh`.
#     A live foreign PID is refused instead of being killed.
#   - A worker with the generic pause marker `tmp/state/<worker>.paused` is left
#     untouched: the operator's intentional stop is not undone by a restart.
#   - A worker without a pid file (never started, or intentionally disabled by
#     config) is skipped, not failed: this is a rollout helper, not a health
#     gate, and a disabled worker must not fail an unrelated deploy.
#   - TERM is intentional rather than HUP: a projected `workers/<name>.sh`
#     cannot replace functions already resident in the old bash process. The
#     existing `start_all.sh` supervisor owns respawn; this helper waits for a
#     new live PID with the reviewed cmdline and fails closed if none appears.
#   - The probes use bash builtins (`kill`, `read`) instead of pipelines: the
#     deployment host runs at load average > 10, where one `sed | cut` per
#     iteration stretched a nominal 10s bound to ~30s and made the wait
#     meaningless. `audio_worker` gets the longest bound because it defers TERM
#     while a playback/queue child is in the foreground.
#   - A worker that does not exit within the bounded TERM wait is *logged and
#     skipped*, not failed: `audio_worker` defers the TERM trap while a
#     playback/queue child is in the foreground, which can exceed any bound a
#     deploy is willing to wait. The TERM is already delivered at that point and
#     `start_all.sh` replaces the process the moment the foreground work ends.
#     Failing the step instead aborted the remaining deploy steps (observed in
#     run 37688998678), which is worse than a deferred replacement.
#   - Output goes to the VM-private exec log. Only the exit code reaches the
#     workflow step log; keep these stable:
#       0   every target was replaced, or skipped (paused / not started /
#           still winding down after a delivered TERM)
#       10  audio_worker: recorded PID alive but is not the reviewed worker
#       12  audio_worker: no live reviewed replacement within the bounded wait
#       20/22 youtube_worker, 30/32 poll_worker, 40/42 prediction_worker,
#       50/52 stream_noon_audit (same two meanings per worker)
#     When several workers fail, the human-readable log records all of them and
#     the exit code is the first failure, so a rerun has a stable meaning.

root="/home/ubuntu/soren"
if [[ "${1:-}" == "--root" ]]; then
  [[ $# -eq 2 ]]
  root="$2"
  shift 2
fi
[[ $# -eq 0 ]]

# Read /proc/<pid>/stat without forking. Bash cannot split on the closing paren
# with a glob, so strip through the last ") " and take the first field.
is_live_pid() {
  local pid="$1" stat="" state=""
  kill -0 "$pid" 2>/dev/null || return 1
  if IFS= read -r stat < "/proc/$pid/stat" 2>/dev/null; then
    state="${stat##*) }"
    state="${state%% *}"
    [[ "$state" == Z ]] && return 1
  fi
  return 0
}

#  0  the recorded PID is alive (whatever it is)
#  1  no pid file, or the recorded PID is already gone (supervisor's business)
recorded_pid_is_live() {
  local pid_file="$root/tmp/state/$1.pid" pid=""
  IFS= read -r pid < "$pid_file" 2>/dev/null || return 1
  pid="${pid//[[:space:]]/}"
  [[ "$pid" =~ ^[1-9][0-9]*$ ]] || return 1
  is_live_pid "$pid" || return 1
  return 0
}

# Prints the live reviewed PID for a worker, or returns 1.
# The cmdline is NUL-separated, so read token by token instead of piping tr.
read_live_worker_pid() {
  local name="$1" pid_file="$root/tmp/state/$1.pid" pid="" token=""
  IFS= read -r pid < "$pid_file" 2>/dev/null || return 1
  pid="${pid//[[:space:]]/}"
  [[ "$pid" =~ ^[1-9][0-9]*$ ]] || return 1
  is_live_pid "$pid" || return 1
  [[ -r "/proc/$pid/cmdline" ]] || return 1
  while IFS= read -r -d '' token; do
    if [[ "$token" == *"workers/${name}.sh"* ]]; then
      printf '%s\n' "$pid"
      return 0
    fi
  done < "/proc/$pid/cmdline"
  return 1
}

# restart_worker <name> <failure_base> <term_wait_iterations> <replace_wait_iterations>
restart_worker() {
  local name="$1" base="$2" term_wait="$3" replace_wait="$4" old_pid="" new_pid=""

  if [[ -e "$root/tmp/state/${name}.paused" ]]; then
    echo "skip: ${name} is intentionally paused (pause marker kept)" >&2
    return 0
  fi

  if ! recorded_pid_is_live "$name"; then
    echo "skip: ${name} has no live recorded PID (not started or already restarting)" >&2
    return 0
  fi

  if ! old_pid="$(read_live_worker_pid "$name")"; then
    echo "${name}: PID in tmp/state/${name}.pid is alive but is not the reviewed worker" >&2
    return "$base"
  fi

  kill -TERM "$old_pid" 2>/dev/null || {
    echo "${name}: TERM failed for PID ${old_pid}" >&2
    return "$base"
  }

  # The old process must actually exit before a replacement can be trusted.
  for _ in $(seq 1 "$term_wait"); do
    is_live_pid "$old_pid" || break
    sleep 0.25
  done
  if is_live_pid "$old_pid"; then
    echo "${name}: TERM delivered to PID ${old_pid}; still winding down after the bounded wait (supervisor replaces it when its foreground work ends)" >&2
    return 0
  fi

  # start_all.sh respawns dead workers. Success requires a *new* live PID that is
  # still the reviewed worker, so a supervisor that gave up is detected.
  for _ in $(seq 1 "$replace_wait"); do
    if new_pid="$(read_live_worker_pid "$name" 2>/dev/null)"; then
      if [[ "$new_pid" != "$old_pid" ]]; then
        echo "${name}: replaced PID ${old_pid} -> ${new_pid}" >&2
        return 0
      fi
    fi
    sleep 0.25
  done
  echo "${name}: no live reviewed replacement appeared within the bounded wait" >&2
  return "$((base + 2))"
}

targets=(audio_worker youtube_worker poll_worker prediction_worker stream_noon_audit)
failure_bases=(10 20 30 40 50)
# audio_worker parks the TERM trap behind a foreground playback/queue child, so
# it gets a longer exit bound (20s) than the others (10s). Exceeding it is a
# logged skip, not a failure -- see the note above.
term_waits=(80 40 40 40 40)
replace_waits=(120 80 80 80 80)

first_failure=0
for idx in "${!targets[@]}"; do
  status=0
  restart_worker "${targets[$idx]}" "${failure_bases[$idx]}" "${term_waits[$idx]}" "${replace_waits[$idx]}" || status=$?
  if [[ "$status" -ne 0 && "$first_failure" -eq 0 ]]; then
    first_failure="$status"
  fi
done

exit "$first_failure"
