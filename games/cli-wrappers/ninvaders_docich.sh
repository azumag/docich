#!/bin/sh
# ninvaders match loop for docich: auto-start and auto-retry.
#
# ninvaders boots to a title screen ("Press SPACE to start") and returns
# there by itself at game over (same process continues).  Pane input only
# reaches the FOREGROUND process, so the game runs in the foreground while
# a background driver loop presses SPACE on the title screen.  The game
# plays unattended with the docich [agent] disabled.  The final Score of
# each match is recorded for the score stats panel (scorelog JSONL): the
# session max is flushed when the title screen reappears.
SCORELOG="${NINVADERS_SCORELOG:-/home/ubuntu/docich/run-soren-live/scores/ninvaders.jsonl}"
PANE="${TMUX_PANE:-}"

record_score() {
  [ "$1" -gt 0 ] 2>/dev/null || return 0
  mkdir -p "$(dirname "$SCORELOG")" 2>/dev/null || true
  printf '{"ts":%s,"game":"ninvaders","score":%s,"source":"wrapper"}\n' "$(date +%s)" "$1" >>"$SCORELOG" 2>/dev/null || true
}

driver() {
  max_score=0
  seen_game=0
  while :; do
    sleep 2
    [ -n "$PANE" ] || continue
    text="$(tmux capture-pane -p -t "$PANE" 2>/dev/null)" || continue
    cur="$(printf '%s' "$text" | grep -oE 'Score: [0-9]+' | tail -1 | grep -oE '[0-9]+')"
    if [ -n "$cur" ]; then
      # Strip leading zeros for POSIX $(( )) (dash has no base#number syntax
      # and treats leading-zero literals as octal, so 0000008 would fail).
      cur="$(printf '%s' "$cur" | sed 's/^0*//')"
      [ -z "$cur" ] && cur=0
      if [ "$cur" -gt "$max_score" ]; then
        max_score="$cur"
      fi
    fi
    case "$text" in
      *"Level:"*) seen_game=1 ;;
    esac
    case "$text" in
      *"Press SPACE to start"*)
        if [ "$seen_game" = "1" ]; then
          record_score "$max_score"
          max_score=0
          seen_game=0
        fi
        tmux send-keys -t "$PANE" Space
        ;;
    esac
  done
}

driver &
DRIVER=$!
/usr/games/ninvaders
rc=$?
kill "$DRIVER" 2>/dev/null
exit "$rc"
