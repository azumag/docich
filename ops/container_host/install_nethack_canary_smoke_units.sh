#!/usr/bin/env bash
# Install the P5i NetHack production canary smoke units.
# Usage: sudo ops/container_host/install_nethack_canary_smoke_units.sh
#
# This script does not add ubuntu to the docker group. Docker socket access is
# granted only to the dedicated oneshot through SupplementaryGroups=docker.
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "error: must run as root" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SERVICE_SRC="${ROOT}/scripts/systemd/docich-nethack-canary-smoke.service"
PATH_SRC="${ROOT}/scripts/systemd/docich-nethack-canary-smoke.path"
SERVICE_DST="/etc/systemd/system/docich-nethack-canary-smoke.service"
PATH_DST="/etc/systemd/system/docich-nethack-canary-smoke.path"

[[ "${ROOT}" == /* ]] || { echo "error: repository root must be absolute" >&2; exit 1; }
[[ -f "${SERVICE_SRC}" && -f "${PATH_SRC}" ]] || { echo "error: reviewed unit templates missing" >&2; exit 1; }
getent passwd ubuntu >/dev/null || { echo "error: ubuntu user missing" >&2; exit 1; }
getent group docker >/dev/null || { echo "error: docker group missing" >&2; exit 1; }
if id -nG ubuntu | tr ' ' '\n' | grep -qx docker; then
  echo "error: ubuntu must not be a permanent docker-group member" >&2
  exit 1
fi
systemctl is-active --quiet docker.service || { echo "error: docker.service is not active" >&2; exit 1; }

tmp_service="$(mktemp)"
tmp_path="$(mktemp)"
trap 'rm -f "${tmp_service}" "${tmp_path}"' EXIT
sed "s|__DOCICH_ROOT__|${ROOT}|g" "${SERVICE_SRC}" >"${tmp_service}"
sed "s|__DOCICH_ROOT__|${ROOT}|g" "${PATH_SRC}" >"${tmp_path}"

# Verify the exact rendered units before touching /etc.
systemd-analyze verify "${tmp_service}" "${tmp_path}" >/dev/null
install -o root -g root -m 0644 "${tmp_service}" "${SERVICE_DST}"
install -o root -g root -m 0644 "${tmp_path}" "${PATH_DST}"
systemctl daemon-reload
systemctl enable --now docich-nethack-canary-smoke.path

# The path watcher is the only persistent component; the Docker-privileged
# service must remain inactive between explicitly reviewed epoch changes.
systemctl is-active --quiet docich-nethack-canary-smoke.path
if systemctl is-active --quiet docich-nethack-canary-smoke.service; then
  echo "error: smoke service unexpectedly active after installation" >&2
  exit 1
fi

printf 'nethack canary smoke units installed: path=active service=inactive\n'
