#!/usr/bin/env bash
set -euo pipefail
# Canonical deploy hook for the Hanjuku month/boss audit issue filer.
# Installs only its own user units and enables the five-minute timer. It does
# not start the service during deploy: the first tick files the sealed audit
# records through the VM's own gh authentication and the vendored scanner.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
root="${DOCICH_PROD_ROOT:-/home/ubuntu/docich}"
service="docich-hanjuku-month-audit.service"
timer="docich-hanjuku-month-audit.timer"

command -v getent >/dev/null 2>&1 || exit 2
home="$(getent passwd "$(id -u)" | cut -d: -f6)" || exit 2
[[ "$home" == "${DOCICH_PROD_HOME:-/home/ubuntu}" ]] || exit 2
unit_dir="${XDG_CONFIG_HOME:-$home/.config}/systemd/user"
[[ "$unit_dir" == "$home"/* ]] || exit 2

# Fail closed before installing a timer that cannot post. gh auth is checked
# by the service itself: this hook may run in a non-interactive SSH session
# without the user bus that the gh credential store can require.
command -v gh >/dev/null 2>&1 || exit 12
[[ -f "$root/ops/vm_actions/secret_scan.py" && ! -L "$root/ops/vm_actions/secret_scan.py" ]] || exit 13

mkdir -p "$unit_dir"
tmp=""
trap 'if [[ -n "$tmp" ]]; then rm -f "$tmp"; fi' EXIT
for unit in "$service" "$timer"; do
  source="$root/scripts/systemd/$unit"
  target="$unit_dir/$unit"
  [[ -f "$source" && ! -L "$source" && ! -L "$target" ]] || exit 2
  tmp="$(mktemp "$unit_dir/.hanjuku-month-audit.XXXXXX")"
  sed "s#__DOCICH_ROOT__#$root#g" "$source" > "$tmp"
  chmod 0644 "$tmp"
  mv -f "$tmp" "$target"
  tmp=""
done

systemctl --user daemon-reload
systemctl --user enable --now "$timer"
systemctl --user is-enabled --quiet "$timer" || exit 2
systemctl --user is-active --quiet "$timer" || exit 2

# Verify the configured state_dir and the recipe without touching GitHub.
timeout 60 /usr/bin/python3 "$root/ops/vm_actions/hanjuku_month_audit_watch.py" --dry-run >/dev/null || exit 14

echo "hanjuku month audit timer installed"
