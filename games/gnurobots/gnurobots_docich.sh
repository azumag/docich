#!/bin/sh
# GNU Robots match loop for docich: keeps the game pane alive across matches.
#
# The game process exits at each match end (program finished / robot died),
# so this wrapper restarts it.  The resolver script is re-read at every
# match start: the improvement loop can hot-swap the script file between
# matches without touching the pane.
MAP="${GNUROBOTS_MAP:-/usr/local/share/gnurobots/maps/small.map}"
SCRIPT="${GNUROBOTS_SCRIPT:-/usr/local/share/gnurobots/resolver.scm}"

while :; do
  /usr/local/bin/gnurobots -f "$MAP" "$SCRIPT"
  sleep 2
done
