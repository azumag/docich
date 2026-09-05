#!/bin/sh
# bastet match loop for docich: auto-start and auto-retry.
#
# bastet boots to a menu ("Play! (normal version)").  At game over it shows
# a dialog ("Try again!"), then the high-score table, then back to the menu;
# one Enter advances each screen (same process continues).  Pane input only
# reaches the FOREGROUND process, so the game runs in the foreground while
# a background driver loop presses Enter on those screens.  The game plays
# unattended with the docich [agent] disabled, and each final Score is
# recorded for the score stats panel (scorelog JSONL).
#
# Known limitation: a score high enough to enter the high-score table opens
# a name-entry screen the driver does not fill (unattended play never
# scores that high; a future bot must handle it).
#
# Shell portability: this file runs under /bin/sh (dash on Ubuntu), which
# has no base#number arithmetic and exits a non-interactive shell on a
# syntax error.  Keep it strictly POSIX (no [[ ]], no $((10#...))).
SCORELOG="${BASTET_SCORELOG:-/home/ubuntu/docich/run-soren-live/scores/bastet.jsonl}"
PANE="${TMUX_PANE:-}"

record_score() {
  [ "$1" -gt 0 ] 2>/dev/null || return 0
  mkdir -p "$(dirname "$SCORELOG")" 2>/dev/null || true
  printf '{"ts":%s,"game":"bastet","score":%s,"source":"wrapper"}\n' "$(date +%s)" "$1" >>"$SCORELOG" 2>/dev/null || true
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
  while :; do
    sleep 2
    [ -n "$PANE" ] || continue
    text="$(tmux capture-pane -p -t "$PANE" 2>/dev/null)" || continue
    cur="$(printf '%s' "$text" | grep -oE 'Score: [0-9]+' | tail -1 | grep -oE '[0-9]+')"
    if [ -n "$cur" ]; then
      cur="$(dec "$cur")"
      if [ "$cur" -gt "$max_score" ]; then
        max_score="$cur"
      fi
    fi
    case "$text" in
      *"Score:"*) seen_game=1 ;;
    esac
    case "$text" in
      *"Try again!"*)
        if [ "$seen_game" = "1" ]; then
          record_score "$max_score"
          max_score=0
          seen_game=0
        fi
        tmux send-keys -t "$PANE" Enter
        sleep 2
        ;;
      *"difficulty"*)
        tmux send-keys -t "$PANE" Enter
        sleep 2
        ;;
      *"Play! (normal version)"*)
        tmux send-keys -t "$PANE" Enter
        sleep 2
        ;;
    esac
  done
}

driver &
DRIVER=$!
/usr/games/bastet
rc=$?
kill "$DRIVER" 2>/dev/null
exit "$rc"
