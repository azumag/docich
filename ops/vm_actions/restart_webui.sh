#!/usr/bin/env bash
set -euo pipefail

# Reviewed, bounded unit reconcile + restart + verification for the docich Web UI.
#
# Safety properties:
#   - No root/sudo. Only the fixed user unit `docich-webui.service` is written,
#     daemon-reloaded and restarted. No arguments, caller-supplied paths or unit
#     names are accepted.
#   - The installed unit is rendered only from the reviewed repository template
#     `scripts/systemd/docich-webui.service`, replacing the single documented
#     `__DOCICH_ROOT__` placeholder with the current production checkout root.
#   - Production exec has no login session, so XDG_RUNTIME_DIR is set
#     explicitly instead of relying on an ambient value.
#   - "Unit is active" is not enough to call this done: the operation succeeds
#     only when the HTML served on the configured local port is byte-identical
#     to the deployed `INDEX_HTML`.
#   - Output goes to the VM-private exec log. The exit code is the only signal
#     that reaches the workflow step log; keep these stable:
#       0   unit reconciled/restarted, active, served HTML == deployed INDEX_HTML
#       10  systemctl restart failed
#       11  unit not active within the bounded wait
#       12  deployed INDEX_HTML unreadable, or ExecStart runs another root
#       13  local webui not reachable on the configured port within the wait
#       14  served HTML stale while ExecStart runs the production root
#       15  reviewed unit template could not be rendered/installed/reloaded
[[ $# -eq 0 ]] || { echo "no arguments accepted" >&2; exit 2; }

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

unit="docich-webui.service"
root="$(pwd -P)"
template="$root/scripts/systemd/docich-webui.service"
unit_dir="$HOME/.config/systemd/user"
unit_path="$unit_dir/$unit"

# Reconcile the fixed user unit before restart. This is required when a reviewed
# template change alters ExecStart (for example, the production --config added
# by #1028); a plain restart would otherwise keep the stale, repo-external unit.
if ! python3 - "$template" "$unit_dir" "$unit_path" "$root" <<'PY'
import os
from pathlib import Path
import sys

template = Path(sys.argv[1])
unit_dir = Path(sys.argv[2])
unit_path = Path(sys.argv[3])
root = sys.argv[4]

try:
    source = template.read_text(encoding="utf-8")
except OSError as exc:
    raise SystemExit(f"cannot read reviewed webui unit template: {exc}")

if "__DOCICH_ROOT__" not in source:
    raise SystemExit("reviewed webui unit template is missing __DOCICH_ROOT__")
rendered = source.replace("__DOCICH_ROOT__", root)
if "__DOCICH_ROOT__" in rendered:
    raise SystemExit("reviewed webui unit template placeholder remained after render")

try:
    unit_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = unit_dir / f".{unit_path.name}.tmp-{os.getpid()}"
    tmp.write_text(rendered, encoding="utf-8")
    tmp.chmod(0o644)
    os.replace(tmp, unit_path)
except OSError as exc:
    try:
        tmp.unlink(missing_ok=True)
    except Exception:
        pass
    raise SystemExit(f"cannot install reviewed webui unit template: {exc}")
PY
then
  echo "reviewed webui unit template reconcile failed" >&2
  exit 15
fi

if ! systemctl --user daemon-reload; then
  echo "systemctl daemon-reload failed" >&2
  exit 15
fi

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

# Probe the same production profile embedded in the reviewed service template.
# No runtime knob can be passed in because production exec scrubs the
# environment to PATH/HOME/LANG.
set +e
python3 - <<'PY'
import hashlib, re, sys, time, urllib.request

try:
    import tomllib
    with open("config/docich.soren-live.toml", "rb") as fh:
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

deadline = time.monotonic() + 6.0
while True:
    for host in hosts:
        url = f"http://{host}:{port}/"
        try:
            served = urllib.request.urlopen(url, timeout=2).read()
        except Exception:
            continue
        print(f"served: {url}", file=sys.stderr)
        sys.exit(0 if hashlib.sha256(served).digest() == expected else 14)
    if time.monotonic() >= deadline:
        sys.exit(13)
    time.sleep(0.2)
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
