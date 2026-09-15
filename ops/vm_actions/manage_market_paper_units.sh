#!/usr/bin/env bash
set -euo pipefail

# Reviewed, bounded systemd --user unit management for the opt-in stocks/FX
# paper-trading corners (src/docich/trading/markets/).
#
# Safety properties:
#   - No root/sudo. Only `systemctl --user` on the fixed unit set already
#     reviewed under scripts/systemd/docich-market-*.
#   - Only two inputs select behaviour, both from a fixed enum, validated
#     below: MARKET_PAPER_ACTION (install/enable/disable/restart) and
#     MARKET_PAPER_MARKET (stocks/fx). No other value is ever interpolated
#     into a systemctl unit name or path.
#   - Never edits config/market-paper.toml (that stays a normal reviewed
#     code change), never touches broker credentials, never sends a real
#     order. install/enable only ever start an *idle* worker: Runtime.tick()
#     itself refuses to trade while enabled=false in that same config file.
#
# Env (set by the owner-only control plane, .github/workflows/vm-operations.yml,
# operation=market_paper):
#   MARKET_PAPER_ACTION=install|enable|disable|restart
#   MARKET_PAPER_MARKET=stocks|fx
#
# Optional flag exists so repository tests can run against a temporary root
# (with a stub `systemctl` earlier on PATH and HOME pointed at a temp dir):
#   --root DIR   (default /home/ubuntu/docich)

root="/home/ubuntu/docich"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) root="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

action="${MARKET_PAPER_ACTION:-}"
market="${MARKET_PAPER_MARKET:-}"
case "$action" in install|enable|disable|restart) ;; *) echo "invalid action: $action" >&2; exit 2 ;; esac
case "$market" in stocks|fx) ;; *) echo "invalid market: $market" >&2; exit 2 ;; esac
[[ -d "$root" ]] || { echo "root not found: $root" >&2; exit 2; }

# Production exec runs this without an interactive login session, so the
# `systemctl --user` D-Bus socket must be located explicitly rather than
# relying on an ambient XDG_RUNTIME_DIR (gateway.py's execute() sets a
# minimal fixed env with no such variable).
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
worker_unit="docich-market-worker@${market}.service"
corner_timer="docich-market-corner@${market}.timer"
improve_timer="docich-market-improve@${market}.timer"

install_units() {
  mkdir -p "$unit_dir" || { echo "mkdir failed: $unit_dir" >&2; exit 10; }
  local templates=(
    docich-market-worker@.service
    docich-market-corner@.service
    docich-market-corner@.timer
    docich-market-improve@.service
    docich-market-improve@.timer
  )
  local name src
  for name in "${templates[@]}"; do
    src="$root/scripts/systemd/$name"
    [[ -f "$src" ]] || { echo "missing template: $src" >&2; exit 11; }
    sed "s#__DOCICH_ROOT__#$root#g" "$src" > "$unit_dir/$name" || { echo "write failed: $unit_dir/$name" >&2; exit 12; }
  done
  systemctl --user daemon-reload || { echo "daemon-reload failed (XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR)" >&2; exit 13; }
  echo "installed: ${templates[*]}"
}

enable_market() {
  systemctl --user enable --now "$worker_unit"
  systemctl --user enable --now "$corner_timer"
  systemctl --user enable --now "$improve_timer"
}

disable_market() {
  systemctl --user disable --now "$worker_unit"
  systemctl --user disable --now "$corner_timer"
  systemctl --user disable --now "$improve_timer"
}

restart_market() {
  systemctl --user restart "$worker_unit"
}

case "$action" in
  install) install_units ;;
  enable) enable_market ;;
  disable) disable_market ;;
  restart) restart_market ;;
esac

# Production exec output is withheld by the gateway regardless of what this
# script prints (ops/vm_actions/gateway.py execute()), so results are
# verified afterwards through the `diagnostics` operation
# (_collect_market_paper in collect_diagnostics.py), not by printing status
# here.
