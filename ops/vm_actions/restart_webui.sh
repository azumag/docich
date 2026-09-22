#!/usr/bin/env bash
set -euo pipefail

# Reviewed, bounded restart for the docich Web UI systemd --user unit.
#
# Properties:
#   - No root/sudo. Only `systemctl --user` on the fixed unit
#     `docich-webui.service`. No caller input is interpolated into any
#     systemctl argument, and no arbitrary command can be passed.
#   - Production exec has no login session, so XDG_RUNTIME_DIR is set
#     explicitly instead of relying on an ambient value (same convention as
#     manage_market_paper_units.sh).
#   - Bounded wait: fail closed unless the unit reports active within 5s.
#   - Prints only the fixed unit name and the post-restart
#     ActiveState/SubState/MainPID/ExecMainStartTimestamp to the VM-private
#     exec log. Never prints environment or file contents.
#
# The production path is invoked by the owner-only `restart_webui` operation
# after a reviewed deploy of `src/docich/webui.py`; it does not deploy code.

[[ $# -eq 0 ]] || { echo "no arguments accepted" >&2; exit 2; }

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

unit="docich-webui.service"

systemctl --user restart "$unit"

for _ in $(seq 1 20); do
  state="$(systemctl --user show "$unit" --property=ActiveState --value 2>/dev/null || true)"
  if [[ "$state" == "active" ]]; then
    systemctl --user show "$unit" --property=ActiveState,SubState,MainPID,ExecMainStartTimestamp
    exit 0
  fi
  sleep 0.25
done

echo "docich-webui is not active after restart" >&2
exit 1
