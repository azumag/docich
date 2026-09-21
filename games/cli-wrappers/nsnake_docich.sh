#!/bin/sh
# nsnake match loop for docich: auto-start Arcade Mode and auto-retry.
#
# nsnake boots to a main menu and shows a "Game Over / Retry?" dialog at
# match end (the same process continues).  Pane input only reaches the
# FOREGROUND process, so the game itself runs in the foreground while a
# background driver loop sends the two transition keys. This wrapper owns
# menu/retry and result recording only, NOT a direction-playing AI: steering
# is the docich [agent] command brain (brains/nsnake/brain.py), which stays
# silent on the menu and "Game Over" dialog.
# Each final Score (including zero) is saved before retry; after MAX_MATCHES
# completed rounds it holds the result screen until the coordinator returns.
SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
. "$SCRIPT_DIR/_run_with_driver.sh"
SCORELOG="${NSNAKE_SCORELOG:-/home/ubuntu/docich/run-soren-live/scores/nsnake.jsonl}"
PANE="${TMUX_PANE:-}"
NSNAKE_BIN="${NSNAKE_BIN:-/usr/games/nsnake}"
DRIVER_INTERVAL="${NSNAKE_DRIVER_INTERVAL:-2}"
MAX_MATCHES="${NSNAKE_MAX_MATCHES:-3}"
case "$MAX_MATCHES" in
  ''|*[!0-9]*|0*) echo "NSNAKE_MAX_MATCHES must be a positive integer" >&2; exit 2 ;;
esac

record_score() {
  # Save the same captured Game Over frame; recapturing may see a new screen.
  score="$(printf '%s' "$1" | grep -oE 'Score [0-9]+' | tail -1 | grep -oE '[0-9]+')"
  [ -n "$score" ] || return 1
  score="$(printf '%s' "$score" | sed 's/^0*//')"
  [ -n "$score" ] || score=0
  mkdir -p "$(dirname "$SCORELOG")" 2>/dev/null || return 1
  printf '{"ts":%s,"game":"nsnake","score":%s,"source":"wrapper"}\n' "$(date +%s)" "$score" >>"$SCORELOG" 2>/dev/null
}

driver() {
  menu_done=0
  retry_armed=1
  matches=0
  while :; do
    sleep "$DRIVER_INTERVAL"
    [ -n "$PANE" ] || continue
    text="$(tmux capture-pane -p -t "$PANE" 2>/dev/null)" || continue
    [ "$matches" -lt "$MAX_MATCHES" ] || continue
    case "$text" in
      *"Main Menu"*)
        if [ "$menu_done" = "0" ]; then
          menu_done=1
          tmux send-keys -t "$PANE" Enter
        fi
        ;;
      *)
        menu_done=0
        ;;
    esac
    case "$text" in
      *"Game Over"*)
        if [ "$retry_armed" = "1" ]; then
          # Keep the result screen on persistence failure; never lose a match.
          record_score "$text" || continue
          retry_armed=0
          matches=$((matches + 1))
          [ "$matches" -lt "$MAX_MATCHES" ] || continue
          sleep 1
          tmux send-keys -t "$PANE" Enter
        fi
        ;;
      *)
        retry_armed=1
        ;;
    esac
  done
}

driver </dev/null &
DRIVER=$!
docich_wrapper_run_with_driver "$DRIVER" "$NSNAKE_BIN"
rc=$?
exit "$rc"
