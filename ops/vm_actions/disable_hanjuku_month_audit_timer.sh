#!/usr/bin/env bash
set -euo pipefail
# One-time cleanup for the retired five-minute audit filing timer. The bot now
# spawns the filer only when it seals an incident, so no timer should remain
# running while the corner is idle. Idempotent.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
service="docich-hanjuku-month-audit.service"
timer="docich-hanjuku-month-audit.timer"

command -v getent >/dev/null 2>&1 || exit 2
home="$(getent passwd "$(id -u)" | cut -d: -f6)" || exit 2
[[ "$home" == "${DOCICH_PROD_HOME:-/home/ubuntu}" ]] || exit 2
unit_dir="${XDG_CONFIG_HOME:-$home/.config}/systemd/user"
[[ "$unit_dir" == "$home"/* ]] || exit 2

systemctl --user disable --now "$timer" >/dev/null 2>&1 || true
systemctl --user stop "$service" >/dev/null 2>&1 || true
rm -f "$unit_dir/$service" "$unit_dir/$timer"
systemctl --user daemon-reload
echo "retired hanjuku month audit timer removed"
