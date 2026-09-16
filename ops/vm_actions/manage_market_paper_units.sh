#!/usr/bin/env bash
set -euo pipefail

# Reviewed, bounded systemd --user unit management for the opt-in stocks/FX
# PAPER corners and their separately controlled read-only market-data providers.
#
# Safety properties:
#   - No root/sudo. Only `systemctl --user` on the fixed unit set already
#     reviewed under scripts/systemd/docich-market-*.
#   - Only two inputs select behaviour, both from a fixed enum, validated
#     below: MARKET_PAPER_ACTION (install/enable/disable/restart/
#     provider-enable/provider-disable/provider-restart/seed-test-quote) and
#     MARKET_PAPER_MARKET (stocks/fx). No other value is ever interpolated
#     into a systemctl unit name or path.
#   - Never edits config/market-paper.toml, never creates broker credentials,
#     never sends an order. PAPER units and market-data provider units are
#     explicitly separate: enabling one never implicitly enables the other.
#   - For market=stocks, install provisions the exact pinned optional
#     read-only Moomoo quote SDK into the existing .venv-trading when that
#     venv exists. It never downloads OpenD, logs in, enables stocks, or
#     creates/imports a trading context. The separately reviewed OpenD unit
#     only uses operator-managed files under $HOME and loopback port 11111.
#   - The stocks provider is the loopback-only Moomoo collector. The FX
#     provider is the OANDA-practice pricing-only collector and requires the
#     operator-managed %h/.config/docich/oanda-practice.env file. This script
#     never creates, reads or prints that credential file.
#   - Provider enable/restart first installs the reviewed unit templates so a
#     first-use provider action cannot depend on a stale/manual unit copy.
#   - seed-test-quote (fx only) writes one fixed-shape deterministic USD_JPY
#     quote for validating the file-feed pipeline. It never reads or fabricates
#     data resembling a real live provider.
#
# Env (set by an owner-only reviewed control plane):
#   MARKET_PAPER_ACTION=install|enable|disable|restart|provider-enable|provider-disable|provider-restart|seed-test-quote
#   MARKET_PAPER_MARKET=stocks|fx
#
# Optional flag exists so repository tests can run against a temporary root:
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
case "$action" in
  install|enable|disable|restart|provider-enable|provider-disable|provider-restart|seed-test-quote) ;;
  *) echo "invalid action: $action" >&2; exit 2 ;;
esac
case "$market" in stocks|fx) ;; *) echo "invalid market: $market" >&2; exit 2 ;; esac
[[ -d "$root" ]] || { echo "root not found: $root" >&2; exit 2; }

# Production exec runs this without an interactive login session, so the
# `systemctl --user` D-Bus socket must be located explicitly rather than
# relying on an ambient XDG_RUNTIME_DIR.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
worker_unit="docich-market-worker@${market}.service"
corner_timer="docich-market-corner@${market}.timer"
improve_timer="docich-market-improve@${market}.timer"
provider_unit="docich-market-data-${market}.service"

install_units() {
  mkdir -p "$unit_dir" || { echo "mkdir failed: $unit_dir" >&2; exit 10; }
  local templates=(
    docich-market-worker@.service
    docich-market-corner@.service
    docich-market-corner@.timer
    docich-market-improve@.service
    docich-market-improve@.timer
    docich-moomoo-opend.service
    docich-market-data-stocks.service
    docich-market-data-fx.service
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

provision_stock_quote_sdk() {
  [[ "$market" == "stocks" ]] || return 0
  local python_bin="$root/.venv-trading/bin/python3"
  local requirements="$root/requirements-market-data.txt"

  # Existing production trading installs already own .venv-trading. Do not
  # silently create a new interpreter environment from this bounded installer.
  if [[ ! -x "$python_bin" ]]; then
    echo "trading venv unavailable; skipped optional stock quote SDK provision" >&2
    return 0
  fi
  [[ -f "$requirements" ]] || { echo "missing market-data requirements: $requirements" >&2; exit 14; }

  "$python_bin" -m pip install --disable-pip-version-check --no-input -r "$requirements" \
    || { echo "market-data SDK install failed" >&2; exit 15; }
  "$python_bin" -c 'import moomoo' >/dev/null 2>&1 \
    || { echo "market-data SDK import failed" >&2; exit 16; }
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

enable_provider() {
  systemctl --user enable --now "$provider_unit"
}

disable_provider() {
  systemctl --user disable --now "$provider_unit"
}

restart_provider() {
  systemctl --user restart "$provider_unit"
}

seed_test_quote() {
  [[ "$market" == "fx" ]] || { echo "seed-test-quote is fx-only" >&2; exit 21; }
  PYTHONPATH="$root/src" python3 - "$root" <<'PY'
import json, os, pathlib, sys, time

sys.path.insert(0, sys.argv[1] + "/src")
from docich.config import load_global  # noqa: E402

root = pathlib.Path(sys.argv[1])
g = load_global(root, root / "config" / "docich.soren-live.toml")
data_root = g.state_dir / "market-data"
data_root.mkdir(parents=True, exist_ok=True)

# Deterministic, reviewed test-only drift: a fixed 0.05% step per call,
# tracked in a counter file next to the feed itself. Never reads or
# fabricates data resembling a real live quote source.
counter_path = data_root / ".test-feed-seed-counter"
try:
    n = int(counter_path.read_text(encoding="utf-8").strip())
except (OSError, ValueError):
    n = 0
n += 1
counter_path.write_text(str(n), encoding="utf-8")

now = int(time.time())
base = 150.00
bid = round(base * (1 + 0.0005 * n), 4)
ask = round(bid + 0.01, 4)
payload = {
    "market": "fx",
    "realtime": True,
    "quotes": [
        {
            "symbol": "USD_JPY",
            "ts": now,
            "bid": f"{bid:.4f}",
            "ask": f"{ask:.4f}",
            "bid_size": "100000",
            "ask_size": "100000",
            "tradeable": True,
            "source": "owner-approved-test-feed",
            "currency": "JPY",
        }
    ],
}
tmp = data_root / "market-fx-quotes.json.tmp"
tmp.write_text(json.dumps(payload), encoding="utf-8")
os.replace(tmp, data_root / "market-fx-quotes.json")
print(f"seeded n={n} ts={now} bid={bid} ask={ask}")
PY
}

case "$action" in
  install) install_units; provision_stock_quote_sdk ;;
  enable) enable_market ;;
  disable) disable_market ;;
  restart) restart_market ;;
  provider-enable) install_units; provision_stock_quote_sdk; enable_provider ;;
  provider-disable) disable_provider ;;
  provider-restart) install_units; provision_stock_quote_sdk; restart_provider ;;
  seed-test-quote) seed_test_quote ;;
esac

# Production exec output is withheld by the gateway regardless of what this
# script prints, so state is verified afterwards through read-only diagnostics.
