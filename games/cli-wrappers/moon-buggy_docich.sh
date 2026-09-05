#!/bin/sh
# moon-buggy match loop for docich: auto-start and auto-retry.
#
# moon-buggy boots to a title screen and shows the high-score table with
# "y,RET:new game" at game over (same process continues).  Pane input only
# reaches the FOREGROUND process, so the game runs in the foreground while
# a background driver loop sends the transition keys.  The game plays
# unattended with the docich [agent] disabled, and each final score is
# recorded for the score stats panel (scorelog JSONL).
#
# Shell portability: this file runs under /bin/sh (dash on Ubuntu), which
# has no base#number arithmetic and exits a non-interactive shell on a
# syntax error.  Keep it strictly POSIX (no [[ ]], no $((10#...))).
SCORELOG="${MOONBUGGY_SCORELOG:-/home/ubuntu/docich/run-soren-live/scores/moon-buggy.jsonl}"
PANE="${TMUX_PANE:-}"

record_score() {
  [ "$1" -gt 0 ] 2>/dev/null || return 0
  mkdir -p "$(dirname "$SCORELOG")" 2>/dev/null || true
  printf '{"ts":%s,"game":"moon-buggy","score":%s,"source":"wrapper"}\n' "$(date +%s)" "$1" >>"$SCORELOG" 2>/dev/null || true
}

dec() {
  # POSIX decimal compare helper: strip leading zeros ("08"/"09" are not
  # valid octal for $(( )) and dash rejects 10# prefixes outright).
  stripped="$(printf '%s' "$1" | sed 's/^0*//')"
  [ -z "$stripped" ] && stripped=0
  printf '%s' "$stripped"
}

driver() {
  max_score=0
  seen_game=0
  started=0
  while :; do
    sleep 2
    [ -n "$PANE" ] || continue
    text="$(tmux capture-pane -p -t "$PANE" 2>/dev/null)" || continue
    cur="$(printf '%s' "$text" | grep -oE 'score: [0-9]+' | tail -1 | grep -oE '[0-9]+')"
    if [ -n "$cur" ]; then
      cur="$(dec "$cur")"
      if [ "$cur" -gt "$max_score" ]; then
        max_score="$cur"
      fi
    fi
    case "$text" in
      *"level:"*) seen_game=1 ;;
    esac
    case "$text" in
      *"new game"*)
        if [ "$seen_game" = "1" ]; then
          record_score "$max_score"
          max_score=0
          seen_game=0
        fi
        tmux send-keys -t "$PANE" -l "y"
        sleep 3
        ;;
      *"start game"*)
        if [ "$started" = "0" ]; then
          started=1
          tmux send-keys -t "$PANE" -l "y"
        fi
        ;;
    esac
  done
}

driver &
DRIVER=$!
/usr/games/moon-buggy
rc=$?
kill "$DRIVER" 2>/dev/null
exit "$rc"
