#!/bin/sh
# bastet match loop for docich: auto-start and auto-retry.
#
# bastet boots to a menu ("Play! (normal version)").  At game over it shows
# a dialog ("Try again!"), then the high-score table, then back to the menu;
# one Enter advances each screen (same process continues).  Pane input only
# reaches the FOREGROUND process, so the game runs in the foreground while
# a background driver loop presses Enter on those screens.  In play the docich
# [agent] command brain (brains/bastet/brain.py) drops the pieces and stays
# silent on these screens.  Each final Score (0 included) is recorded for the
# score stats panel (scorelog JSONL).
#
# Known limitation: a score high enough to enter the high-score table opens
# a name-entry screen the driver does not fill (unattended play never
# scores that high; a future bot must handle it).
#
# Shell portability: this file runs under /bin/sh (dash on Ubuntu), which
# has no base#number arithmetic and exits a non-interactive shell on a
# syntax error.  Keep it strictly POSIX (no [[ ]], no $((10#...))).
SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
. "$SCRIPT_DIR/_run_with_driver.sh"
SCORELOG="${BASTET_SCORELOG:-/home/ubuntu/docich/run-soren-live/scores/bastet.jsonl}"
PANE="${TMUX_PANE:-}"
BASTET_BIN="${BASTET_BIN:-/usr/games/bastet}"
MAX_MATCHES="${BASTET_MAX_MATCHES:-${DOCICH_TARGET_MATCHES:-3}}"
case "$MAX_MATCHES" in
  ''|*[!0-9]*|0*) echo "BASTET_MAX_MATCHES must be a positive integer" >&2; exit 2 ;;
esac

record_score() {
  # A completed match is recorded even at 0: the brain cannot see the board
  # (coloured blanks), so 0-point matches are normal, and the corner counts
  # recorded matches to end early.  Non-numeric input is still skipped.
  [ "$1" -ge 0 ] 2>/dev/null || return 0
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
  matches=0
  while :; do
    sleep 2
    [ -n "$PANE" ] || continue
    text="$(tmux capture-pane -p -t "$PANE" 2>/dev/null)" || continue
    # The score is right-aligned ("Score:      0"), so allow any run of spaces.
    cur="$(printf '%s' "$text" | grep -oE 'Score: *[0-9]+' | tail -1 | grep -oE '[0-9]+')"
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
          matches=$((matches + 1))
          max_score=0
          seen_game=0
        fi
        [ "$matches" -lt "$MAX_MATCHES" ] || continue
        tmux send-keys -t "$PANE" Enter
        sleep 2
        ;;
      *"difficulty"*)
        tmux send-keys -t "$PANE" Enter
        sleep 2
        ;;
      *"Play! (normal version)"*)
        if [ "$seen_game" = "1" ]; then
          # The menu is only reached after a match.  If "Try again!" was
          # dismissed before this driver saw it (an Enter the brain already
          # had in flight does that), flush the finished match here.
          record_score "$max_score"
          matches=$((matches + 1))
          max_score=0
          seen_game=0
        fi
        [ "$matches" -lt "$MAX_MATCHES" ] || continue
        tmux send-keys -t "$PANE" Enter
        sleep 2
        ;;
    esac
  done
}

driver </dev/null &
DRIVER=$!
docich_wrapper_run_with_driver "$DRIVER" "$BASTET_BIN"
rc=$?
exit "$rc"
