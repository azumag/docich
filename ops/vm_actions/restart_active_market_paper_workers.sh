#!/usr/bin/env bash
set -euo pipefail

# Refresh reviewed PAPER/runtime code after a production deploy. Existing
# resident workers/providers are restarted in place, and the scheduled crypto
# PAPER corner's independent deadline watchdog is installed so a wedged corner
# cannot hold the dashboard indefinitely.
#
# Safety properties:
# - stocks/FX units are restarted only when already active;
# - crypto `trading` is reloaded only when that exact tmux window exists;
# - watchdog enablement follows [paper_corner].enabled from reviewed config;
# - no broker credentials, live-order paths, stream/radio units, or arbitrary
#   commands are touched.

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
DOCICH_PROD_ROOT="${DOCICH_PROD_ROOT:-/home/ubuntu/docich}"

install_paper_watchdog() {
  local config="$DOCICH_PROD_ROOT/config/docich.soren-live.toml"
  local python_bin="$DOCICH_PROD_ROOT/.venv-trading/bin/python3"
  local unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
  local timer="docich-paper-corner-watchdog.timer"
  local enabled

  if [[ ! -x "$python_bin" ]]; then
    python_bin=python3
  fi
  enabled="$("$python_bin" - "$config" <<'PY'
import pathlib, sys, tomllib
raw = tomllib.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
value = raw.get('paper_corner', {}).get('enabled', False)
if type(value) is not bool:
    raise SystemExit('paper_corner.enabled must be boolean')
print('1' if value else '0')
PY
)"

  if [[ "$enabled" != "1" ]]; then
    systemctl --user disable --now "$timer" >/dev/null 2>&1 || true
    return 0
  fi

  mkdir -p "$unit_dir"
  local name src
  for name in docich-paper-corner-watchdog.service docich-paper-corner-watchdog.timer; do
    src="$DOCICH_PROD_ROOT/scripts/systemd/$name"
    [[ -f "$src" ]] || { echo "missing watchdog unit template: $src" >&2; return 21; }
    sed "s#__DOCICH_ROOT__#$DOCICH_PROD_ROOT#g" "$src" > "$unit_dir/$name"
  done
  systemctl --user daemon-reload
  systemctl --user enable --now "$timer"
}

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

install_paper_watchdog
