#!/usr/bin/env bash
set -euo pipefail

# Fixed read-only probe executed only through the owner-only production VM
# gateway. The gateway withholds stdout/stderr from production exec; this helper
# additionally emits nothing and communicates only through a small fixed exit
# code vocabulary consumed by the trusted GitHub Actions workflow.
ROOT="$(pwd -P)"
[[ -f "$ROOT/src/docich/trading/markets/provider_probe.py" ]] || exit 16

python_bin=python3
if [[ -x "$ROOT/.venv-trading/bin/python3" ]]; then
  python_bin="$ROOT/.venv-trading/bin/python3"
fi

set +e
probe_json="$(PYTHONPATH="$ROOT/src" "$python_bin" -m docich.trading.markets.provider_probe \
  --provider moomoo --host 127.0.0.1 --port 11111 --symbols JP.7203,JP.7974 2>/dev/null)"
probe_rc=$?
set -e
[[ "$probe_rc" -eq 0 && -n "$probe_json" && ${#probe_json} -le 8192 ]] || exit 16

status="$(python3 - "$probe_json" <<'PY'
import json, sys
allowed = {
    "realtime_ready",
    "entitled_not_realtime_ready",
    "jp_quote_unavailable",
    "sdk_unavailable",
    "opend_unreachable",
    "probe_failed",
    "invalid_probe_request",
}
try:
    data = json.loads(sys.argv[1])
except (TypeError, ValueError):
    raise SystemExit(1)
if not isinstance(data, dict):
    raise SystemExit(1)
if data.get("provider") != "moomoo" or data.get("mode") != "read_only_quote_probe":
    raise SystemExit(1)
status = data.get("status")
if status not in allowed:
    raise SystemExit(1)
print(status)
PY
)" || exit 16

case "$status" in
  realtime_ready) exit 0 ;;
  entitled_not_realtime_ready) exit 10 ;;
  jp_quote_unavailable) exit 11 ;;
  sdk_unavailable) exit 12 ;;
  opend_unreachable) exit 13 ;;
  probe_failed) exit 14 ;;
  invalid_probe_request) exit 15 ;;
  *) exit 16 ;;
esac
