#!/bin/sh
# Shared lifecycle guard for CLI game wrappers.
#
# tmux stops a pane by signalling its shell, but a shell that is waiting for a
# foreground game is not required to forward that signal to the game. Run the
# game as a tracked child, stop its driver first, and reap both children on
# every exit path so a rotation cannot leave an orphan game behind.

_docich_wrapper_children() {
  ps -eo pid=,ppid= 2>/dev/null | awk -v parent="$1" '$2 == parent {print $1}'
}

_docich_wrapper_running() {
  pid="$1"
  kill -0 "$pid" 2>/dev/null || return 1
  state="$(ps -p "$pid" -o stat= 2>/dev/null | tr -d '[:space:]')"
  [ -n "$state" ] || return 1
  case "$state" in
    Z*) return 1 ;;
  esac
  return 0
}

_docich_wrapper_stop_tree() {
  case "$1" in
    ''|*[!0-9]*|0) return 0 ;;
  esac

  # Stop leaves first. This prevents the root shell from exiting and
  # reparenting a still-running sleep/tmux/driver child.
  # Each recursive call runs in a subshell because POSIX sh has no portable
  # local-variable declaration. Without that isolation, the recursive
  # function's loop variables overwrite the parent's root PID and the driver
  # is left alive after the game exits; its inherited capture pipe then keeps
  # the caller blocked forever on Linux.
  for child in $(_docich_wrapper_children "$1"); do
    (_docich_wrapper_stop_tree "$child")
  done

  kill -TERM "$1" 2>/dev/null || true
  waited=0
  while _docich_wrapper_running "$1" && [ "$waited" -lt 10 ]; do
    sleep 0.1
    waited=$((waited + 1))
  done
  if _docich_wrapper_running "$1"; then
    kill -KILL "$1" 2>/dev/null || true
  fi
  # The driver and game are direct children of this wrapper. wait() is the
  # actual zombie-prevention step; kill alone only changes them into zombies.
  wait "$1" 2>/dev/null || true
}

docich_wrapper_run_with_driver() {
  driver_pid="$1"
  shift
  game_pid=0
  cleanup_done=0

  docich_wrapper_cleanup() {
    [ "$cleanup_done" -eq 0 ] || return 0
    cleanup_done=1
    _docich_wrapper_stop_tree "$driver_pid"
    _docich_wrapper_stop_tree "$game_pid"
  }

  docich_wrapper_signal() {
    signal_name="$1"
    docich_wrapper_cleanup
    trap - HUP INT TERM
    if [ "$signal_name" = "INT" ]; then
      exit 130
    fi
    exit 143
  }

  trap 'docich_wrapper_signal HUP' HUP
  trap 'docich_wrapper_signal INT' INT
  trap 'docich_wrapper_signal TERM' TERM

  # POSIX sh connects an asynchronous command's stdin to /dev/null when job
  # control is disabled unless that command has an explicit stdin redirection.
  # These games are interactive terminal programs, so preserve the wrapper's
  # tmux pane stdin explicitly while still keeping the game PID trackable.
  exec 9<&0
  "$@" <&9 9<&- &
  game_pid=$!
  exec 9<&-
  wait "$game_pid"
  rc=$?
  trap - HUP INT TERM
  docich_wrapper_cleanup
  return "$rc"
}
