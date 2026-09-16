#!/usr/bin/env bash
set -euo pipefail

# Reload reviewed PAPER/runtime code after a production deploy without changing
# opt-in state. Only already-active resident workers and read-only market-data
# collectors are restarted; disabled/inactive runtimes are left untouched.
#
# This helper deliberately does NOT:
# - enable/start an inactive unit or tmux worker;
# - install units or dependencies;
# - edit trading config;
# - touch broker credentials or any real-order path.

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
DOCICH_PROD_ROOT="${DOCICH_PROD_ROOT:-/home/ubuntu/docich}"

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

# Crypto PAPER predates the stocks/FX systemd workers and still lives in the
# shared `docich:trading` tmux window. A normal code deploy updates files but a
# live Python process keeps its old imports, so reload it only when that exact
# worker is already present. Use bash explicitly: production deployment may
# preserve the reviewed file contents while not making this helper executable.
# The operator itself preserves the SQLite ledger, restarts only `trading`, and
# verifies that the replacement pane stays alive.
if tmux has-session -t docich >/dev/null 2>&1 \
  && tmux list-windows -t docich -F '#{window_name}' 2>/dev/null | grep -Fxq 'trading'; then
  bash "$DOCICH_PROD_ROOT/bin/docich-paper-corner-operator" \
    --config "$DOCICH_PROD_ROOT/config/docich.soren-live.toml" \
    --reload-worker >/dev/null
fi
