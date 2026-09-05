#!/bin/sh
# pacman4console match loop for docich: auto-start and auto-retry.
#
# pacman4console needs at least a 29x32 pane (the docich toml declares
# rows=32 for this game).  It boots to a title ("Press any key...") and
# shows "Game Over / ... or any other key to play again" at match end
# (same process continues).  Pane input only reaches the FOREGROUND
# process, so the game runs in the foreground while a background driver
# loop sends the transition keys.  The game plays unattended with the
# docich [agent] disabled, and each final Score is recorded for the score
# stats panel (scorelog JSONL).
SCORELOG="${PACMAN_SCORELOG:-/home/ubuntu/docich/run-soren-live/scores/pacman4console.jsonl}"
PANE="${TMUX_PANE:-}"
LEVEL="${PACMAN_LEVEL:-1}"

record_score() {
  [ "$1" -gt 0 ] 2>/dev/null || return 0
  mkdir -p "$(dirname "$SCORELOG")" 2>/dev/null || true
  printf '{"ts":%s,"game":"pacman4console","score":%s,"source":"wrapper"}\n' "$(date +%s)" "$1" >>"$SCORELOG" 2>/dev/null || true
}

driver() {
  max_score=0
  seen_game=0
  started=0
  while :; do
    sleep 2
    [ -n "$PANE" ] || continue
    text="$(tmux capture-pane -p -t "$PANE" 2>/dev/null)" || continue
    cur="$(printf '%s' "$text" | grep -oE 'Score: [0-9]+' | tail -1 | grep -oE '[0-9]+')"
    if [ -n "$cur" ]; then
      # Strip leading zeros for POSIX $(( )) (dash has no base#number syntax
      # and treats leading-zero literals as octal, so e.g. 8/9 would fail).
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
      *"Game Over"*)
        if [ "$seen_game" = "1" ]; then
          record_score "$max_score"
          max_score=0
          seen_game=0
        fi
        # "any other key" restarts; SPACE is guaranteed not to be 'q'.
        tmux send-keys -t "$PANE" -l " "
        sleep 3
        ;;
      *"Press any key..."*)
        if [ "$started" = "0" ]; then
          started=1
          tmux send-keys -t "$PANE" -l " "
        fi
        ;;
    esac
  done
}

driver &
DRIVER=$!
/usr/games/pacman4console --level="$LEVEL"
rc=$?
kill "$DRIVER" 2>/dev/null
exit "$rc"
