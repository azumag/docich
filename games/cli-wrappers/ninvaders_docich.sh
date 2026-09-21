#!/bin/sh
# ninvaders match loop for docich: auto-start, auto-play and auto-retry.
#
# ninvaders boots to a title screen ("Press SPACE to start") and returns
# there by itself at game over (same process continues). Pane input only
# reaches the FOREGROUND process, so the game runs in the foreground while
# a background driver loop handles the title screen and a low-latency
# deterministic baseline player. The baseline sweeps left/right while firing;
# it is intentionally simple so the Soren improvement loop can replace it with
# a better policy later without depending on an LLM for frame-level input.
# The final Score of each match is recorded for the score stats panel
# (scorelog JSONL): the session max is flushed when the title screen reappears.
#
# Usage: ninvaders_docich.sh [brain]
#   (no argument) the baseline sweep player below plays the match.
#   brain         the docich [agent] command brain (brains/ninvaders/brain.py)
#                 plays; this wrapper only starts matches and records scores, so
#                 the two never send competing keys.
SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
. "$SCRIPT_DIR/_run_with_driver.sh"
SCORELOG="${NINVADERS_SCORELOG:-/home/ubuntu/docich/run-soren-live/scores/ninvaders.jsonl}"
PANE="${TMUX_PANE:-}"
NINVADERS_BIN="${NINVADERS_BIN:-/usr/games/ninvaders}"
DRIVER_INTERVAL="${NINVADERS_DRIVER_INTERVAL:-0.35}"
MAX_MATCHES="${NINVADERS_MAX_MATCHES:-3}"
SELFPLAY=1
[ "${1:-}" = "brain" ] && SELFPLAY=0

case "$MAX_MATCHES" in
  ''|*[!0-9]*|0*) echo "NINVADERS_MAX_MATCHES must be a positive integer" >&2; exit 2 ;;
esac

record_score() {
  [ "$1" -ge 0 ] 2>/dev/null || return 1
  mkdir -p "$(dirname "$SCORELOG")" 2>/dev/null || return 1
  printf '{"ts":%s,"game":"ninvaders","score":%s,"source":"wrapper"}\n' "$(date +%s)" "$1" >>"$SCORELOG" 2>/dev/null
}

driver() {
  max_score=0
  seen_game=0
  matches=0
  direction=Right
  move_ticks=0
  while :; do
    sleep "$DRIVER_INTERVAL"
    [ -n "$PANE" ] || continue
    text="$(tmux capture-pane -p -t "$PANE" 2>/dev/null)" || continue
    [ "$matches" -lt "$MAX_MATCHES" ] || continue
    cur="$(printf '%s' "$text" | grep -oE 'Score: [0-9]+' | tail -1 | grep -oE '[0-9]+')"
    if [ -n "$cur" ]; then
      # Strip leading zeros for POSIX $(( )) (dash has no base#number syntax
      # and treats leading-zero literals as octal, so 0000008 would fail).
      cur="$(printf '%s' "$cur" | sed 's/^0*//')"
      [ -z "$cur" ] && cur=0
      if [ "$seen_game" = "1" ] && [ "$cur" -lt "$max_score" ]; then
        # The score only falls when a new match has begun.  A key the brain had
        # already in flight when the title screen appeared starts the next match
        # at once, so this driver may never see that screen: flush it here.
        record_score "$max_score" || continue
        max_score=0
        seen_game=0
        matches=$((matches + 1))
      fi
      if [ "$cur" -gt "$max_score" ]; then
        max_score="$cur"
      fi
    fi
    case "$text" in
      *"Press SPACE to start"*)
        if [ "$seen_game" = "1" ]; then
          # Persistence failure must not silently discard this match or
          # start the next one. Retry saving while holding the title screen.
          record_score "$max_score" || continue
          max_score=0
          seen_game=0
          matches=$((matches + 1))
        fi
        # Observe while parked; no input is withdrawn from a running match.
        [ "$matches" -lt "$MAX_MATCHES" ] || continue
        tmux send-keys -t "$PANE" Space
        ;;
      *"Level:"*)
        seen_game=1
        [ "$SELFPLAY" = "1" ] || continue
        # nInvaders controls are cursor left/right + SPACE. Repeated keypresses
        # give us a small, bounded low-latency player instead of merely starting
        # a match and then leaving the cannon idle.
        tmux send-keys -t "$PANE" "$direction" Space
        move_ticks=$((move_ticks + 1))
        if [ "$move_ticks" -ge 8 ]; then
          if [ "$direction" = "Right" ]; then
            direction=Left
          else
            direction=Right
          fi
          move_ticks=0
        fi
        ;;
    esac
  done
}

driver </dev/null &
DRIVER=$!
docich_wrapper_run_with_driver "$DRIVER" "$NINVADERS_BIN"
rc=$?
exit "$rc"
