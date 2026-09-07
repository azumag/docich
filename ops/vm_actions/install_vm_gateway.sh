#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "run with sudo/root" >&2
  exit 1
fi
if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "usage: $0 PUBLIC_KEY_FILE [ssh-user]" >&2
  exit 2
fi

pubkey_file=$1
ssh_user=${2:-ubuntu}
source_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
gateway_source="$source_dir/gateway.py"

[[ -f "$gateway_source" && -f "$pubkey_file" ]]
id "$ssh_user" >/dev/null 2>&1
home_dir=$(getent passwd "$ssh_user" | cut -d: -f6)
group_name=$(id -gn "$ssh_user")
[[ -n "$home_dir" && -d "$home_dir" ]]
[[ -d /home/ubuntu/docich && -d /home/ubuntu/soren ]]
command -v git >/dev/null 2>&1 || { echo "git is required" >&2; exit 1; }
[[ -x /usr/bin/bwrap ]] || { echo "bubblewrap is required at /usr/bin/bwrap for isolated preview exec" >&2; exit 1; }
[[ "$(git -C /home/ubuntu/docich rev-parse --is-inside-work-tree 2>/dev/null)" == true ]] || {
  echo "/home/ubuntu/docich must remain a git worktree" >&2
  exit 1
}

read -r key_type key_body _ < "$pubkey_file"
[[ "$key_type" == ssh-ed25519 && "$key_body" =~ ^[A-Za-z0-9+/=]+$ ]]

install -d -o root -g root -m 0755 /usr/local/libexec/azumag-vm-ops
install -o root -g root -m 0755 "$gateway_source" /usr/local/libexec/azumag-vm-ops/gateway.py
install -o root -g root -m 0755 "$source_dir/stage_repair.py" /usr/local/libexec/azumag-vm-ops/stage_repair.py

# Ubuntu 24.04 restricts unprivileged user namespaces through AppArmor by default.
# Keep the host-wide restriction enabled and allow userns only for the operator-only
# bwrap copy that the gateway resolves first via /usr/local/bin in its fixed PATH.
install -o root -g "$group_name" -m 0750 /usr/bin/bwrap /usr/local/bin/bwrap
apparmor_profile=/etc/apparmor.d/usr.local.bin.bwrap
cat > "$apparmor_profile" <<'PROFILE'
abi <abi/4.0>,
include <tunables/global>

/usr/local/bin/bwrap flags=(unconfined) {
  userns,
  include if exists <local/usr.local.bin.bwrap>
}
PROFILE
chown root:root "$apparmor_profile"
chmod 0644 "$apparmor_profile"
if command -v apparmor_parser >/dev/null 2>&1; then
  apparmor_parser -r "$apparmor_profile"
elif [[ -r /proc/sys/kernel/apparmor_restrict_unprivileged_userns ]] && [[ "$(cat /proc/sys/kernel/apparmor_restrict_unprivileged_userns)" == 1 ]]; then
  echo "AppArmor userns restriction is active but apparmor_parser is unavailable" >&2
  exit 1
fi

# Existing root-owned registrations and runtime paths are operator state.
# Reinstallation upgrades executable code, never resets this configuration.
if [[ -e /etc/azumag-vm-ops.json ]]; then
  /usr/bin/python3 - /etc/azumag-vm-ops.json <<'PYCONFIG'
import json, os, stat, sys
from pathlib import Path
p=Path(sys.argv[1]);st=p.lstat()
if not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
    raise SystemExit('existing VM configuration is not trusted')
json.loads(p.read_text())
PYCONFIG
else
  cat > /etc/azumag-vm-ops.json <<'JSON'
{
  "state": "/home/ubuntu/.local/state/github-vm-ops",
  "repos": {
    "docich": {"production": "/home/ubuntu/docich", "mode": "git", "projections": {"games/soviet_now": "/home/ubuntu/soren"}}
  },
  "repair_policies": {}
}
JSON
fi

chown root:root /etc/azumag-vm-ops.json
chmod 0644 /etc/azumag-vm-ops.json

install -d -o "$ssh_user" -g "$group_name" -m 0700 /home/ubuntu/.local/state/github-vm-ops
install -d -o "$ssh_user" -g "$group_name" -m 0700 "$home_dir/.ssh"
authorized="$home_dir/.ssh/authorized_keys"
touch "$authorized"
chown "$ssh_user:$group_name" "$authorized"
chmod 0600 "$authorized"

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
grep -v ' github-vm-ops-actions$' "$authorized" > "$tmp" || true
printf '%s %s %s github-vm-ops-actions\n' \
  'restrict,command="/usr/bin/python3 /usr/local/libexec/azumag-vm-ops/gateway.py /etc/azumag-vm-ops.json"' \
  "$key_type" "$key_body" >> "$tmp"
install -o "$ssh_user" -g "$group_name" -m 0600 "$tmp" "$authorized"

echo "VM gateway installed for $ssh_user. The key is forced through the owner-gated gateway."
