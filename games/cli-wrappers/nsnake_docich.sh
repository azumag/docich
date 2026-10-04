#!/bin/sh
# nsnake match loop for docich: auto-start Arcade Mode and auto-retry.
#
# nsnake boots to a main menu and shows a "Game Over / Retry?" dialog at
# match end (the same process continues).  Pane input only reaches the
# FOREGROUND process, so the game itself runs in the foreground while a
# background driver loop sends the start/retry keys. This wrapper owns initial
# speed selection, menu/retry and result recording, NOT a direction-playing AI: steering
# is the docich [agent] command brain (brains/nsnake/brain.py), which stays
# silent on the menu and "Game Over" dialog.
# NSNAKE_SPEED selects Starting Speed in Game Settings before starting (1-9).
# Each final Score (including zero) is saved before retry; after MAX_MATCHES
# completed rounds it holds the result screen until the coordinator returns.
SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
. "$SCRIPT_DIR/_run_with_driver.sh"
SCORELOG="${NSNAKE_SCORELOG:-${DOCICH_STATE_DIR:-/home/ubuntu/docich/run-soren-live}/scores/nsnake.jsonl}"
PANE="${TMUX_PANE:-}"
NSNAKE_BIN="${NSNAKE_BIN:-/usr/games/nsnake}"
DRIVER_INTERVAL="${NSNAKE_DRIVER_INTERVAL:-2}"
MAX_MATCHES="${NSNAKE_MAX_MATCHES:-${DOCICH_TARGET_MATCHES:-3}}"
GAME_SPEED="${NSNAKE_SPEED:-4}"
case "$MAX_MATCHES" in
  ''|*[!0-9]*|0*) echo "NSNAKE_MAX_MATCHES must be a positive integer" >&2; exit 2 ;;
esac
case "$GAME_SPEED" in
  [1-9]) ;;
  *) echo "NSNAKE_SPEED must be an integer from 1 to 9" >&2; exit 2 ;;
esac

record_score() {
  # Save the same captured Game Over frame; recapturing may see a new screen.
  score="$(printf '%s' "$1" | grep -oE 'Score [0-9]+' | tail -1 | grep -oE '[0-9]+')"
  [ -n "$score" ] || return 1
  score="$(printf '%s' "$score" | sed 's/^0*//')"
  [ -n "$score" ] || score=0
  mkdir -p "$(dirname "$SCORELOG")" 2>/dev/null || return 1
  printf '{"ts":%s,"game":"nsnake","score":%s,"source":"wrapper"}\n' "$(date +%s)" "$score" >>"$SCORELOG" 2>/dev/null || return $?
  docich_wrapper_record_clip nsnake "$score"
}

# Keep separate inputs readable by nSnake's input loop. Never continue a
# partially failed navigation or send a number to an unverified numberbox.
menu_keys() {
  for menu_key do
    if ! tmux send-keys -t "$PANE" "$menu_key"; then
      menu_phase=blocked
      echo "NSNAKE initial speed setup blocked: key send failed" >&2
      return 1
    fi
    sleep 0.1
  done
}

driver() {
  menu_phase=menu
  retry_armed=1
  matches=0
  while :; do
    sleep "$DRIVER_INTERVAL"
    [ -n "$PANE" ] || continue
    text="$(tmux capture-pane -p -t "$PANE" 2>/dev/null)" || continue
    [ "$matches" -lt "$MAX_MATCHES" ] || continue
    case "$text" in
      *"Game Over"*)
        [ "$menu_phase" != "running" ] || menu_phase=menu
        ;;
      *"Main Menu"*)
        case "$menu_phase" in
          menu)
            # Main Menu's ordinary Arcade Mode item ignores numeric keys.
            # Home anchors the cursor before entering the third menu item.
            menu_phase=settings
            menu_keys Home Down Down Enter
            ;;
          start)
            menu_phase=running
            menu_keys Home Enter
            ;;
        esac
        ;;
      *"Game Settings"*)
        case "$menu_phase" in
          settings)
            case "$text" in
              *"Starting Speed"*)
                menu_phase=focus
                menu_keys Home Down
                ;;
            esac
            ;;
          focus)
            # nSnake renders the selected numberbox as <N>, others as [N].
            if printf '%s\n' "$text" | grep -qE 'Starting Speed[[:space:]]*<[0-9]+>'; then
              menu_phase=value
              menu_keys "$GAME_SPEED"
            fi
            ;;
          value)
            if printf '%s\n' "$text" | grep -qE "Starting Speed[[:space:]]*<$GAME_SPEED>"; then
              # Enter on the numberbox RESETS its value. Select Back first.
              menu_phase=start
              menu_keys Home Enter
            fi
            ;;
        esac
        ;;
      *)
        [ "$menu_phase" != "running" ] || menu_phase=menu
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

docich_wrapper_seed_clips nsnake
driver </dev/null &
DRIVER=$!
docich_wrapper_run_with_driver "$DRIVER" "$NSNAKE_BIN"
rc=$?
exit "$rc"
