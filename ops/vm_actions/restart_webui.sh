#!/usr/bin/env bash
set -euo pipefail

# Reviewed, bounded restart + verification for the docich Web UI unit.
#
# Safety properties:
#   - No root/sudo. Only `systemctl --user` on the fixed unit
#     `docich-webui.service`. No arguments, no caller-supplied unit name and
#     no arbitrary command; the only non-read-only action is the restart.
#   - Production exec has no login session, so XDG_RUNTIME_DIR is set
#     explicitly instead of relying on an ambient value (same convention as
#     manage_market_paper_units.sh).
#   - "Unit is active" is not enough to call this done: a stale process that
#     already holds the port would keep serving the old UI. The operation
#     therefore succeeds only when the HTML served on the configured local
#     port is byte-identical to the `INDEX_HTML` of the deployed
#     `src/docich/webui.py` (the production cwd of the exec).
#   - Output goes to the VM-private exec log. The exit code is the only
#     signal that reaches the workflow step log; keep these stable:
#       0   restarted, active, served HTML == deployed INDEX_HTML
#       10  systemctl restart failed
#       11  unit not active within the bounded wait
#       12  deployed INDEX_HTML unreadable, or ExecStart runs another root
#       13  local webui not reachable on the configured port
#       14  served HTML stale while ExecStart runs the production root
#           (another process holds the port, or the restart missed it)
[[ $# -eq 0 ]] || { echo "no arguments accepted" >&2; exit 2; }

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

unit="docich-webui.service"

if ! systemctl --user restart "$unit"; then
  echo "systemctl restart failed" >&2
  exit 10
fi

active=0
for _ in $(seq 1 20); do
  state="$(systemctl --user show "$unit" --property=ActiveState --value 2>/dev/null || true)"
  if [[ "$state" == "active" ]]; then active=1; break; fi
  sleep 0.25
done
if [[ "$active" -ne 1 ]]; then
  echo "docich-webui is not active after restart" >&2
  exit 11
fi
systemctl --user show "$unit" --property=ActiveState,SubState,MainPID,ExecMainStartTimestamp

# The local endpoint comes from the same config the service reads; no knob can
# be passed in because production exec scrubs the environment to
# PATH/HOME/LANG. Loopback is tried first, then the configured bind.
set +e
python3 - <<'PY'
import hashlib, re, sys, urllib.request

try:
    import tomllib
    with open("config/docich.toml", "rb") as fh:
        webui_cfg = (tomllib.load(fh).get("webui") or {})
except Exception:
    webui_cfg = {}

try:
    port = int(webui_cfg.get("port", 8787) or 8787)
except Exception:
    port = 8787
bind = str(webui_cfg.get("bind", "") or "")
hosts = ["127.0.0.1", "localhost"]
if bind and bind not in hosts and bind not in ("0.0.0.0", "::", "[::]"):
    hosts.append(bind)

try:
    text = open("src/docich/webui.py", encoding="utf-8").read()
except OSError:
    sys.exit(12)
match = re.search(r'INDEX_HTML = r"""(.*?)"""', text, re.S)
if not match:
    sys.exit(12)
expected = hashlib.sha256(match.group(1).encode("utf-8")).digest()

for host in hosts:
    url = f"http://{host}:{port}/"
    try:
        served = urllib.request.urlopen(url, timeout=10).read()
    except Exception:
        continue
    print(f"served: {url}", file=sys.stderr)
    sys.exit(0 if hashlib.sha256(served).digest() == expected else 14)
sys.exit(13)
PY
check_rc=$?
set -e

case "$check_rc" in
  0) exit 0 ;;
  13)
    echo "local webui not reachable on the configured port" >&2
    exit 13
    ;;
  14)
    # Distinguish the two stale-HTML causes without printing paths: does the
    # unit's ExecStart actually run the production checkout we verified?
    python3 - <<'PY'
import os, re, subprocess, sys

root = os.path.realpath(os.getcwd())
try:
    out = subprocess.run(
        ["systemctl", "--user", "show", "docich-webui.service", "--property=ExecStart"],
        capture_output=True, text=True, timeout=30,
    ).stdout
except Exception:
    out = ""
match = re.search(r"path=([^ ;]+)", out)
exec_path = os.path.realpath(match.group(1)) if match else ""
if exec_path.startswith(root + os.sep):
    print("served HTML is stale while ExecStart runs the production root "
          "(another process holds the port, or the restart missed it)", file=sys.stderr)
    sys.exit(14)
print("unit ExecStart does not run the production root", file=sys.stderr)
sys.exit(12)
PY
    ;;
  *)
    echo "deployed INDEX_HTML is unreadable" >&2
    exit 12
    ;;
esac
