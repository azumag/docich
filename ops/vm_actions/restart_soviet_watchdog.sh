#!/usr/bin/env bash
set -euo pipefail

# Reviewed, bounded targeted restart of the supervised `soviet_watchdog` process.
#
# Purpose (azumag/docich#970): `soviet_watchdog.sh` is a resident bash process
# started once by `start_all.sh`. It sources `lib/poll_wait.sh` at startup and
# keeps the resulting `docich_poll_sleep` function resident for its whole life,
# so a reviewed forkless-wait change is NOT active in production until the
# process is actually replaced. The existing targeted restart helpers identify
# their targets through `tmp/state/<name>.pid`, but the watchdog owns its
# singleton through the directory lock `tmp/state/.soviet_watchdog.lock` (its
# `owner` file) and has no pid file, so `restart_poll_workers.sh` /
# `restart_chat_kick_workers.sh` cannot replace it. This is the fixed, reviewed
# path for exactly that one process. It does not generalize the pid-file helpers
# and accepts no caller-controlled worker name.
#
# Safety properties:
#   - No root privileges and no caller input. The production root is fixed;
#     `--root` exists only so repository tests can exercise the helper without
#     touching /home/ubuntu/soren.
#   - Only the singleton `soviet_watchdog` process is signalled. The Soren loop,
#     bridge, display, encoder, audio/radio/chat/poll workers and Twitch units
#     are never touched.
#   - The PID recorded in `tmp/state/.soviet_watchdog.lock/owner` is signalled
#     only when it is alive and its `/proc/<pid>/cmdline` still contains
#     `soviet_watchdog.sh`. A live foreign PID is refused instead of killed. A
#     watchdog that was never started is skipped, not failed.
#   - A watchdog held down by the operator (`tmp/state/soviet_watchdog.paused`)
#     or parked by a game-only lifecycle handover (`game_lifecycle_bridge_parked`,
#     where `start_all.sh` deliberately does not respawn it) is left untouched.
#   - TERM is intentional rather than HUP: a projected `soviet_watchdog.sh`
#     cannot replace functions already resident in the old bash process. The
#     existing `start_all.sh` supervisor owns respawn (it polls every ~3s and
#     also adopts a live watchdog it finds via its own process pattern); this
#     helper waits for a *new* live reviewed lock owner and fails closed if none
#     appears.
#   - Output goes to the VM-private exec log. Only the exit code reaches the
#     workflow step log; keep these stable:
#       0   the watchdog was replaced, or skipped (paused / parked / not started
#           / already restarting)
#       10  the live lock owner is not the reviewed watchdog (refused)
#       11  the old PID survived the bounded TERM wait
#       12  no live reviewed replacement appeared within the bounded wait
#     The exit code is the first failure so a rerun has a stable meaning.

root="/home/ubuntu/soren"
if [[ "${1:-}" == "--root" ]]; then
  [[ $# -eq 2 ]]
  root="$2"
  shift 2
fi
[[ $# -eq 0 ]]

lock_owner_file="$root/tmp/state/.soviet_watchdog.lock/owner"
pause_marker="$root/tmp/state/soviet_watchdog.paused"

# `start_all.sh` will not respawn the watchdog while a game-only lifecycle
# handover is parked. Source the predicate in an isolated subshell so this
# helper neither pollutes its environment nor depends on it existing
# (partial/older live trees degrade to "not parked").
bridge_parked() {
  local lib="$root/lib/game_lifecycle.sh"
  [[ -r "$lib" ]] || return 1
  GAME_LIFECYCLE_ROOT="$root" \
  TMP_STATE_DIR="$root/tmp/state" \
  GAME_LIFECYCLE_DIR="${SOREN_GAME_LIFECYCLE_DIR:-$root/tmp/state/game_lifecycle}" \
  GAME_LIFECYCLE_PY="$root/lib/game_lifecycle.py" \
  GAME_LIFECYCLE_ENABLED="${GAME_LIFECYCLE_ENABLED:-1}" \
  bash -c 'source "$1" >/dev/null 2>&1 && command -v game_lifecycle_bridge_parked >/dev/null 2>&1 && game_lifecycle_bridge_parked' \
    _ "$lib" 2>/dev/null
}

# Read /proc/<pid>/stat without forking to treat a zombie as exited (the
# supervisor only respawns once the old process is reaped).
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

# Prints the live reviewed watchdog PID from its lock owner file, or returns 1.
# The cmdline is NUL-separated, so read token by token instead of piping tr.
read_live_watchdog_pid() {
  local pid="" token=""
  IFS= read -r pid < "$lock_owner_file" 2>/dev/null || return 1
  pid="${pid//[[:space:]]/}"
  [[ "$pid" =~ ^[1-9][0-9]*$ ]] || return 1
  is_live_pid "$pid" || return 1
  [[ -r "/proc/$pid/cmdline" ]] || return 1
  while IFS= read -r -d '' token; do
    if [[ "$token" == *"soviet_watchdog.sh"* ]]; then
      printf '%s\n' "$pid"
      return 0
    fi
  done < "/proc/$pid/cmdline"
  return 1
}

if [[ -e "$pause_marker" ]]; then
  echo "skip: soviet_watchdog is intentionally paused (pause marker kept)" >&2
  exit 0
fi

if bridge_parked; then
  echo "skip: game-only lifecycle handover is parked (start_all withholds respawn)" >&2
  exit 0
fi

old_pid=""
if ! old_pid="$(read_live_watchdog_pid)"; then
  # Either the watchdog was never started, is already restarting, or the lock
  # owner is present but is not the reviewed process. Distinguish the last case
  # (a foreign live PID must be refused, not silently skipped) from absence.
  if [[ -r "$lock_owner_file" ]]; then
    raw_owner="$(tr -d '[:space:]' < "$lock_owner_file" 2>/dev/null || true)"
    if [[ "$raw_owner" =~ ^[1-9][0-9]*$ ]] && is_live_pid "$raw_owner"; then
      echo "soviet_watchdog: lock owner PID ${raw_owner} is alive but is not the reviewed watchdog" >&2
      exit 10
    fi
  fi
  echo "skip: no live supervised soviet_watchdog (lock owner missing, stale, or not started)" >&2
  exit 0
fi

kill -TERM "$old_pid" 2>/dev/null || {
  echo "soviet_watchdog: TERM failed for PID ${old_pid}" >&2
  exit 11
}

# The old process must actually exit (and release its lock) before a
# replacement can be trusted. `soviet_watchdog.sh` traps TERM and removes the
# lock dir, so a short bound is enough.
for _ in $(seq 1 40); do
  is_live_pid "$old_pid" || break
  sleep 0.25
done
if is_live_pid "$old_pid"; then
  echo "soviet_watchdog: old PID ${old_pid} still alive after TERM" >&2
  exit 11
fi

# start_all.sh respawns dead workers (~3s poll) and re-creates the lock with the
# new owner. Success requires a *new* live PID that is still the reviewed
# watchdog, so a supervisor that gave up or parked is detected.
for _ in $(seq 1 80); do
  if new_pid="$(read_live_watchdog_pid 2>/dev/null)"; then
    if [[ "$new_pid" != "$old_pid" ]]; then
      echo "soviet_watchdog: replaced PID ${old_pid} -> ${new_pid}" >&2
      exit 0
    fi
  fi
  sleep 0.25
done
echo "soviet_watchdog: no live reviewed replacement appeared within the bounded wait" >&2
exit 12
