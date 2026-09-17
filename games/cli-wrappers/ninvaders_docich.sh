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
SCORELOG="${NINVADERS_SCORELOG:-/home/ubuntu/docich/run-soren-live/scores/ninvaders.jsonl}"
PANE="${TMUX_PANE:-}"
NINVADERS_BIN="${NINVADERS_BIN:-/usr/games/ninvaders}"
DRIVER_INTERVAL="${NINVADERS_DRIVER_INTERVAL:-0.35}"
MAX_MATCHES="${NINVADERS_MAX_MATCHES:-3}"

record_score() {
  [ "$1" -gt 0 ] 2>/dev/null || return 0
  mkdir -p "$(dirname "$SCORELOG")" 2>/dev/null || true
  printf '{"ts":%s,"game":"ninvaders","score":%s,"source":"wrapper"}\n' "$(date +%s)" "$1" >>"$SCORELOG" 2>/dev/null || true
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
      *"Press SPACE to start"*)
        if [ "$seen_game" = "1" ]; then
          record_score "$max_score"
          max_score=0
          seen_game=0
          matches=$((matches + 1))
          # 指定試合数を完走したら自動開始しない (コーナー終了を待つ)。
          if [ "$matches" -ge "$MAX_MATCHES" ] 2>/dev/null; then
            break
          fi
        fi
        tmux send-keys -t "$PANE" Space
        ;;
      *"Level:"*)
        seen_game=1
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

driver &
DRIVER=$!
"$NINVADERS_BIN"
rc=$?
kill "$DRIVER" 2>/dev/null
exit "$rc"
