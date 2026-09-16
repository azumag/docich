#!/usr/bin/env bash
set -euo pipefail

# Reload reviewed market PAPER runtime code after a production deploy without
# changing opt-in state.  Only already-active resident workers are restarted;
# disabled/inactive stock or FX workers are left untouched.
#
# This helper deliberately does NOT:
# - enable/start an inactive unit;
# - install units or dependencies;
# - edit config/market-paper.toml;
# - touch broker credentials or any real-order path.

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

for market in stocks fx; do
  unit="docich-market-worker@${market}.service"
  if systemctl --user is-active --quiet "$unit"; then
    systemctl --user restart "$unit"
  fi
done
