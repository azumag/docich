#!/usr/bin/env bash
set -euo pipefail

# Reviewed, bounded targeted restart of the two resident chat workers.
#
# Purpose (azumag/docich#262): `chat_worker` / `kick_worker` source
# `broadcast/comment.sh` once at startup and keep the sourced functions in the
# running bash process. A projected Soren runtime change (for example the
# body-based comment dedup from docich#261 / soviet_now#287) is therefore not
# active in production until those two processes are actually replaced. The
# owner-only control plane had no targeted operation for that, so activating a
# reviewed runtime update required either an ad-hoc SSH session or a full
# unrelated restart. This helper is the fixed, reviewed path for exactly the two
# chat workers and nothing else.
#
# Safety properties:
#   - No root privileges and no caller input. The production root is fixed;
#     `--root` exists only so repository tests can exercise the helper without
#     touching /home/ubuntu/soren.
#   - Only `chat_worker` and `kick_worker` are signalled. The display, Soren,
#     audio, radio, prediction and stream units are never touched.
#   - A PID recorded in `tmp/state/<worker>.pid` is signalled only when it is
#     alive and its `/proc/<pid>/cmdline` still contains `workers/<worker>.sh`.
#     A stale or foreign PID is refused instead of being killed.
#   - A worker with the generic pause marker `tmp/state/<worker>.paused` is left
#     untouched: the operator's intentional stop is not undone by a restart.
#   - TERM is intentional rather than HUP: a projected `workers/<name>.sh`
#     cannot replace functions already resident in the old bash process. The
#     existing `start_all.sh` supervisor owns respawn; this helper waits for a
#     new live PID with the reviewed cmdline and fails closed if none appears.
#   - Output goes to the VM-private exec log. Only the exit code reaches the
#     workflow step log; keep these stable:
#       0   both target workers were replaced (or are intentionally paused)
#       10  chat_worker: no live reviewed process in tmp/state/chat_worker.pid
#       11  chat_worker: old PID survived the bounded TERM wait
#       12  chat_worker: no live reviewed replacement within the bounded wait
#       20  kick_worker: no live reviewed process in tmp/state/kick_worker.pid
#       21  kick_worker: old PID survived the bounded TERM wait
#       22  kick_worker: no live reviewed replacement within the bounded wait
#     When both workers fail, the human-readable log records both; the exit code
#     is the first (chat_worker) failure so a rerun has a stable meaning.

root="/home/ubuntu/soren"
if [[ "${1:-}" == "--root" ]]; then
  [[ $# -eq 2 ]]
  root="$2"
  shift 2
fi
[[ $# -eq 0 ]]

read_live_worker_pid() {
  local name="$1" pid_file="$root/tmp/state/$1.pid" pid="" cmdline=""
  [[ -r "$pid_file" ]] || return 1
  pid="$(tr -d '[:space:]' < "$pid_file" 2>/dev/null || true)"
  [[ "$pid" =~ ^[1-9][0-9]*$ ]] || return 1
  is_live_pid "$pid" || return 1
  [[ -r "/proc/$pid/cmdline" ]] || return 1
  cmdline="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
  [[ "$cmdline" == *"workers/${name}.sh"* ]] || return 1
  printf '%s\n' "$pid"
}

# A signalled worker can linger as a zombie until the supervisor reaps it, and
# `kill -0` still succeeds for a zombie. Treat Z as exited so the bounded waits
# below measure the same thing start_all.sh measures.
is_live_pid() {
  local pid="$1" state=""
  kill -0 "$pid" 2>/dev/null || return 1
  if [[ -r "/proc/$pid/stat" ]]; then
    state="$(sed 's/.*) //' "/proc/$pid/stat" 2>/dev/null | cut -d' ' -f1)"
    [[ "$state" == Z ]] && return 1
  fi
  return 0
}

# restart_worker <name> <failure_base>
#   0                    replaced, or intentionally paused
#   <failure_base>       no live reviewed worker to replace
#   <failure_base>+1     old PID survived TERM
#   <failure_base>+2     no live reviewed replacement appeared
restart_worker() {
  local name="$1" base="$2" old_pid="" new_pid=""

  if [[ -e "$root/tmp/state/${name}.paused" ]]; then
    echo "skip: ${name} is intentionally paused (pause marker kept)" >&2
    return 0
  fi

  if ! old_pid="$(read_live_worker_pid "$name")"; then
    echo "${name}: no live supervised ${name} (tmp/state/${name}.pid missing, stale, or not the reviewed worker)" >&2
    return "$base"
  fi

  kill -TERM "$old_pid" 2>/dev/null || {
    echo "${name}: TERM failed for PID ${old_pid}" >&2
    return "$base"
  }

  # The old process must actually exit before a replacement can be trusted.
  for _ in $(seq 1 40); do
    is_live_pid "$old_pid" || break
    sleep 0.25
  done
  if is_live_pid "$old_pid"; then
    echo "${name}: old PID ${old_pid} still alive after TERM" >&2
    return "$((base + 1))"
  fi

  # start_all.sh respawns dead workers. Success requires a *new* live PID that is
  # still the reviewed worker, so a supervisor that gave up is detected.
  for _ in $(seq 1 80); do
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

targets=(chat_worker kick_worker)
failure_bases=(10 20)

first_failure=0
for idx in "${!targets[@]}"; do
  status=0
  restart_worker "${targets[$idx]}" "${failure_bases[$idx]}" || status=$?
  if [[ "$status" -ne 0 && "$first_failure" -eq 0 ]]; then
    first_failure="$status"
  fi
done

exit "$first_failure"
