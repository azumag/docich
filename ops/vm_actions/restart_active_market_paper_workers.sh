#!/usr/bin/env bash
set -euo pipefail

# Reload reviewed market PAPER/runtime code after a production deploy without
# changing opt-in state. Only already-active resident PAPER workers and
# read-only market-data collectors are restarted; disabled/inactive units are
# left untouched.
#
# This helper deliberately does NOT:
# - enable/start an inactive unit;
# - install units or dependencies;
# - edit config/market-paper.toml;
# - touch broker credentials or any real-order path.

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

for market in stocks fx; do
  worker_unit="docich-market-worker@${market}.service"
  if systemctl --user is-active --quiet "$worker_unit"; then
    systemctl --user restart "$worker_unit"
  fi

  provider_unit="docich-market-data-${market}.service"
  if systemctl --user is-active --quiet "$provider_unit"; then
    systemctl --user restart "$provider_unit"
  fi
done
