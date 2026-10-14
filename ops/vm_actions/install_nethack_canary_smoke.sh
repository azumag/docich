#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "run with sudo/root" >&2
  exit 1
fi
if [[ $# -ne 0 ]]; then
  echo "usage: $0" >&2
  exit 2
fi

root=/home/ubuntu/docich
user=ubuntu
state=/home/ubuntu/.local/state/docich/nethack-canary-smoke
service=docich-nethack-canary-smoke.service
path_unit=docich-nethack-canary-smoke.path

[[ -d "$root/.git" || -f "$root/.git" ]]
[[ "$(git -C "$root" rev-parse --is-inside-work-tree 2>/dev/null)" == true ]]
id "$user" >/dev/null 2>&1
getent group docker >/dev/null
command -v systemctl >/dev/null
command -v sed >/dev/null

members="$(getent group docker | cut -d: -f4)"
if tr ',' '\n' <<<"$members" | grep -Fxq "$user"; then
  echo "refusing persistent docker-group membership for $user" >&2
  exit 1
fi
if [[ "$(id -gn "$user")" == docker ]]; then
  echo "refusing docker as primary group for $user" >&2
  exit 1
fi

"$root/ops/container_host/verify_container_host.sh"

install -d -o "$user" -g "$(id -gn "$user")" -m 0700 "$state" "$state/results"

tmp_service="$(mktemp)"
tmp_path="$(mktemp)"
trap 'rm -f "$tmp_service" "$tmp_path"' EXIT
sed "s|__DOCICH_ROOT__|$root|g" "$root/scripts/systemd/$service" >"$tmp_service"
cat "$root/scripts/systemd/$path_unit" >"$tmp_path"
install -o root -g root -m 0644 "$tmp_service" "/etc/systemd/system/$service"
install -o root -g root -m 0644 "$tmp_path" "/etc/systemd/system/$path_unit"

systemctl daemon-reload
systemctl enable --now "$path_unit"
systemctl is-active --quiet "$path_unit"

props="$(systemctl show "$service" \
  -p User -p SupplementaryGroups -p CPUQuotaPerSecUSec -p MemoryMax -p TasksMax -p FragmentPath)"
grep -Fxq 'User=ubuntu' <<<"$props"
grep -Eq '^SupplementaryGroups=(.* )?docker( .*)?$' <<<"$props"
grep -Fxq 'CPUQuotaPerSecUSec=1s' <<<"$props"
grep -Fxq 'MemoryMax=2147483648' <<<"$props"
grep -Fxq 'TasksMax=128' <<<"$props"
grep -Fxq 'FragmentPath=/etc/systemd/system/docich-nethack-canary-smoke.service' <<<"$props"

echo "NetHack canary smoke path unit installed; no smoke episode was started."
