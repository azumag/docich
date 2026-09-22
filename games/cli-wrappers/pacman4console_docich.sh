#!/bin/sh
# pacman4console match loop for docich: auto-start and auto-retry.
#
# pacman4console needs at least a 29x32 pane (the docich toml declares
# cols=29 / rows=32 and the presentation widens the captured window so the
# terminal's 1:2 cells display square).  It boots to a title ("Press any
# key...") and shows "Game Over / ... or any other key to play again" at
# match end (same process continues).  Pane input only reaches the FOREGROUND
# process, so the game runs in the foreground while a background driver
# loop sends the transition keys.  In play the docich [agent] command brain
# (brains/pacman4console/brain.py) steers and stays silent on the title and
# "Game Over" screens.  Each final Score is recorded for the score stats
# panel (scorelog JSONL).
SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
. "$SCRIPT_DIR/_run_with_driver.sh"
SCORELOG="${PACMAN_SCORELOG:-/home/ubuntu/docich/run-soren-live/scores/pacman4console.jsonl}"
PANE="${TMUX_PANE:-}"
LEVEL="${PACMAN_LEVEL:-1}"
PACMAN_BIN="${PACMAN_BIN:-/usr/games/pacman4console}"
MAX_MATCHES="${PACMAN_MAX_MATCHES:-${DOCICH_TARGET_MATCHES:-3}}"
case "$MAX_MATCHES" in
  ''|*[!0-9]*|0*) echo "PACMAN_MAX_MATCHES must be a positive integer" >&2; exit 2 ;;
esac

record_score() {
  [ "$1" -ge 0 ] 2>/dev/null || return 0
  mkdir -p "$(dirname "$SCORELOG")" 2>/dev/null || true
  printf '{"ts":%s,"game":"pacman4console","score":%s,"source":"wrapper"}\n' "$(date +%s)" "$1" >>"$SCORELOG" 2>/dev/null || true
}

driver() {
  max_score=0
  seen_game=0
  started=0
  matches=0
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
      if [ "$seen_game" = "1" ] && [ "$cur" -lt "$max_score" ]; then
        # The score only ever falls when a new match has begun.  Any key the
        # brain had already in flight when "Game Over" appeared restarts the
        # game at once ("... or any other key to play again"), so this driver
        # may never see that screen: flush the finished match here instead.
        record_score "$max_score"
        matches=$((matches + 1))
        max_score=0
        seen_game=0
        [ "$matches" -lt "$MAX_MATCHES" ] || continue
      fi
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
          matches=$((matches + 1))
          max_score=0
          seen_game=0
        fi
        [ "$matches" -lt "$MAX_MATCHES" ] || continue
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

driver </dev/null &
DRIVER=$!
docich_wrapper_run_with_driver "$DRIVER" "$PACMAN_BIN" --level="$LEVEL"
rc=$?
exit "$rc"
