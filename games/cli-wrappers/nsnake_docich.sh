#!/bin/sh
# nsnake match loop for docich: auto-start Arcade Mode and auto-retry.
#
# nsnake boots to a main menu and shows a "Game Over / Retry?" dialog at
# match end (the same process continues).  Pane input only reaches the
# FOREGROUND process, so the game itself runs in the foreground while a
# background driver loop sends the two transition keys.  The game plays
# unattended with the docich [agent] disabled, and each final Score is
# recorded for the score stats panel (scorelog JSONL).
SCORELOG="${NSNAKE_SCORELOG:-/home/ubuntu/docich/run-soren-live/scores/nsnake.jsonl}"
PANE="${TMUX_PANE:-}"

record_score() {
  score="$(tmux capture-pane -p -t "$PANE" 2>/dev/null | grep -oE 'Score [0-9]+' | tail -1 | grep -oE '[0-9]+')"
  [ -n "$score" ] || return 0
  mkdir -p "$(dirname "$SCORELOG")" 2>/dev/null || true
  printf '{"ts":%s,"game":"nsnake","score":%s,"source":"wrapper"}\n' "$(date +%s)" "$score" >>"$SCORELOG" 2>/dev/null || true
}

driver() {
  menu_done=0
  retry_armed=1
  while :; do
    sleep 2
    [ -n "$PANE" ] || continue
    text="$(tmux capture-pane -p -t "$PANE" 2>/dev/null)" || continue
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
          retry_armed=0
          record_score
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

driver &
DRIVER=$!
/usr/games/nsnake
rc=$?
kill "$DRIVER" 2>/dev/null
exit "$rc"
