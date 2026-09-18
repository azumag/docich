#!/bin/sh
# moon-buggy match loop for docich: auto-start and auto-retry.
#
# moon-buggy boots to a title screen and shows the high-score table with
# "y,RET:new game" at game over (same process continues).  Pane input only
# reaches the FOREGROUND process, so the game runs in the foreground while
# a background driver loop sends the transition keys (a ranking score first
# stops at a "please enter your name" prompt, which Enter accepts).  In play the
# docich [agent] command brain (brains/moon-buggy/brain.py) jumps and fires and
# stays silent on those screens.  Each final score is recorded for the score
# stats panel (scorelog JSONL).
#
# Shell portability: this file runs under /bin/sh (dash on Ubuntu), which
# has no base#number arithmetic and exits a non-interactive shell on a
# syntax error.  Keep it strictly POSIX (no [[ ]], no $((10#...))).
SCORELOG="${MOONBUGGY_SCORELOG:-/home/ubuntu/docich/run-soren-live/scores/moon-buggy.jsonl}"
PANE="${TMUX_PANE:-}"
MOONBUGGY_BIN="${MOONBUGGY_BIN:-/usr/games/moon-buggy}"

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
      *"enter your name"*)
        # A score that ranks in the high-score table stops at a name prompt
        # before the "new game" screen.  Enter accepts the default name.
        tmux send-keys -t "$PANE" Enter
        sleep 2
        ;;
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
"$MOONBUGGY_BIN"
rc=$?
kill "$DRIVER" 2>/dev/null
exit "$rc"
