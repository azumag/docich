#!/usr/bin/env bash
# Install the reviewed container-runtime host state (Docker Engine + gVisor runsc).
# Usage: sudo ops/container_host/install_container_host.sh [--check]
#   --check : verify only, delegate to verify_container_host.sh, modify nothing.
# Safety: this installer never manages host packet-filter rules, never adds any
# user to the docker group, never publishes container ports, never enables a TCP
# Docker socket, and never configures rootless mode. Firewall invariants are
# captured before any change and re-checked after; any drift aborts the run.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/pins.env"
VERIFY_SCRIPT="${SCRIPT_DIR}/verify_container_host.sh"

if [[ "${1:-}" == "--check" ]]; then
  exec bash "${VERIFY_SCRIPT}"
fi
if [[ $# -gt 0 ]]; then
  echo "usage: $0 [--check]" >&2
  exit 2
fi

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "error: must be run as root (use sudo)" >&2
  exit 1
fi

# --- Host guard: Ubuntu 24.04 arm64 only ---
if [[ ! -r /etc/os-release ]]; then
  echo "error: /etc/os-release not readable; refusing to proceed" >&2
  exit 1
fi
# shellcheck disable=SC1091
source /etc/os-release
if [[ "${ID:-}" != "ubuntu" || "${VERSION_ID:-}" != "24.04" ]]; then
  echo "error: requires Ubuntu 24.04 (got ID=${ID:-unknown} VERSION_ID=${VERSION_ID:-unknown})" >&2
  exit 1
fi
ARCH="$(dpkg --print-architecture)"
if [[ "${ARCH}" != "arm64" ]]; then
  echo "error: requires arm64 (got ${ARCH})" >&2
  exit 1
fi
if [[ "${DOCKER_ARCH}" != "arm64" || "${DOCKER_SUITE}" != "noble" ]]; then
  echo "error: pins.env mismatch (DOCKER_ARCH/DOCKER_SUITE)" >&2
  exit 1
fi
if [[ ! "${GVISOR_APT_SUITE}" =~ ^[0-9]{8}$ ]]; then
  echo "error: pins.env mismatch (GVISOR_APT_SUITE must be YYYYMMDD)" >&2
  exit 1
fi

command -v python3 >/dev/null 2>&1 || { echo "error: python3 is required" >&2; exit 1; }
command -v gpg >/dev/null 2>&1 || { echo "error: gpg is required" >&2; exit 1; }
command -v systemctl >/dev/null 2>&1 || { echo "error: systemctl is required" >&2; exit 1; }
if ! command -v iptables >/dev/null 2>&1 && ! command -v iptables-save >/dev/null 2>&1; then
  echo "error: iptables inspection is required; refusing to assume zero Docker rules" >&2
  exit 1
fi
if ! command -v ss >/dev/null 2>&1 && ! command -v netstat >/dev/null 2>&1; then
  echo "error: socket-listener inspection is required; refusing to assume Docker API ports are closed" >&2
  exit 1
fi

# --- Firewall baseline (captured BEFORE any change) ---
# Expected hard invariants of the reviewed state: zero packet-filter rules
# matching DOCKER, forwarding sysctls both 0, no TCP listeners on the Docker
# API ports. Abort when the baseline already violates them, because the host
# is then not in the reviewed state.
DOCKER_TCP_FIRST=2375
DOCKER_TCP_SECOND=$((DOCKER_TCP_FIRST + 1))

iptables_docker_count() {
  local rules=""
  if command -v iptables >/dev/null 2>&1; then
    if ! rules="$(iptables -S 2>/dev/null)"; then
      echo unknown
      return
    fi
  else
    if ! rules="$(iptables-save 2>/dev/null)"; then
      echo unknown
      return
    fi
  fi
  grep -c DOCKER <<<"${rules}" || true
}

tcp_listen_count() {
  local port="$1" listeners=""
  if command -v ss >/dev/null 2>&1; then
    if ! listeners="$(ss -ltn 2>/dev/null)"; then
      echo unknown
      return
    fi
  else
    if ! listeners="$(netstat -ltn 2>/dev/null)"; then
      echo unknown
      return
    fi
  fi
  awk -v p=":${port}$" '$4 ~ p {c++} END {print c+0}' <<<"${listeners}"
}

capture_firewall_baseline() {
  BASE_IPTABLES_DOCKER="$(iptables_docker_count)"
  BASE_IPV4_FORWARD="$(sysctl -n net.ipv4.ip_forward 2>/dev/null || echo unknown)"
  BASE_IPV6_FORWARD="$(sysctl -n net.ipv6.conf.all.forwarding 2>/dev/null || echo unknown)"
  BASE_LISTEN_FIRST="$(tcp_listen_count "${DOCKER_TCP_FIRST}")"
  BASE_LISTEN_SECOND="$(tcp_listen_count "${DOCKER_TCP_SECOND}")"
  echo "firewall baseline: iptables_docker_rules=${BASE_IPTABLES_DOCKER} ipv4_forwarding=${BASE_IPV4_FORWARD} ipv6_forwarding=${BASE_IPV6_FORWARD} listen_first=${BASE_LISTEN_FIRST} listen_second=${BASE_LISTEN_SECOND}"
}

assert_firewall_invariants() {
  local context="$1"
  local docker_rules="$2" v4="$3" v6="$4" l1="$5" l2="$6"
  if [[ "${docker_rules}" != "0" ]]; then
    echo "error: ${context}: expected 0 packet-filter rules matching DOCKER, got ${docker_rules}" >&2
    exit 1
  fi
  if [[ "${v4}" != "0" ]]; then
    echo "error: ${context}: expected net.ipv4.ip_forward=0, got ${v4}" >&2
    exit 1
  fi
  if [[ "${v6}" != "0" ]]; then
    echo "error: ${context}: expected net.ipv6.conf.all.forwarding=0, got ${v6}" >&2
    exit 1
  fi
  if [[ "${l1}" != "0" ]]; then
    echo "error: ${context}: unexpected TCP listener on port ${DOCKER_TCP_FIRST} (got ${l1})" >&2
    exit 1
  fi
  if [[ "${l2}" != "0" ]]; then
    echo "error: ${context}: unexpected TCP listener on Docker API port (got ${l2})" >&2
    exit 1
  fi
}

capture_firewall_baseline
assert_firewall_invariants "firewall baseline" \
  "${BASE_IPTABLES_DOCKER}" "${BASE_IPV4_FORWARD}" "${BASE_IPV6_FORWARD}" \
  "${BASE_LISTEN_FIRST}" "${BASE_LISTEN_SECOND}"

apt_changed=0
daemon_changed=0

# --- daemon.json must be safe before Docker packages can start dockerd ---
# Docker packages may enable/start the daemon from maintainer scripts. Seed the
# reviewed no-forwarding/no-iptables configuration before installing them so a
# clean rebuild never briefly starts Docker with its networking defaults.
daemon_json_matches_pin() {
  python3 - "${DOCKER_DAEMON_JSON}" <<'PYEOF' >/dev/null 2>&1
import json, sys
pinned = {"ip-forward": False, "ip6tables": False, "iptables": False,
          "runtimes": {"runsc": {"path": "/usr/bin/runsc"}}}
with open(sys.argv[1], encoding="utf-8") as fh:
    current = json.load(fh)
raise SystemExit(0 if current == pinned else 1)
PYEOF
}

write_pinned_daemon_json() {
  install -d -m 0755 /etc/docker
  cat >"${DOCKER_DAEMON_JSON}" <<'DAEMONJSON'
{
  "ip-forward": false,
  "ip6tables": false,
  "iptables": false,
  "runtimes": {
    "runsc": {
      "path": "/usr/bin/runsc"
    }
  }
}
DAEMONJSON
  chmod 0644 "${DOCKER_DAEMON_JSON}"
}

install -d -m 0755 /etc/docker
if [[ ! -e "${DOCKER_DAEMON_JSON}" ]]; then
  write_pinned_daemon_json
  daemon_changed=1
elif ! daemon_json_matches_pin; then
  if [[ "${FORCE_DAEMON_JSON:-0}" == "1" ]]; then
    write_pinned_daemon_json
    daemon_changed=1
  else
    echo "error: ${DOCKER_DAEMON_JSON} differs from pin; refusing before package installation without FORCE_DAEMON_JSON=1" >&2
    exit 1
  fi
fi

# --- Prerequisites ---
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install --no-install-recommends -y ca-certificates curl gnupg

# --- Docker APT key (fingerprint pinned) ---
install -d -m 0755 /etc/apt/keyrings
docker_key_tmp="$(mktemp)"
trap 'rm -f "${docker_sources_tmp:-}" "${docker_key_tmp:-}" "${gvisor_key_tmp:-}" "${gvisor_key_tmp:-}.dearmored" "${gvisor_list_tmp:-}"' EXIT
curl -fsSL "${DOCKER_UBUNTU_URL}/gpg" -o "${docker_key_tmp}"
mapfile -t _want_fprs < <(tr ',' '\n' <<<"${DOCKER_KEY_FINGERPRINTS}")
_got_fprs="$(gpg --show-keys --with-colons "${docker_key_tmp}" 2>/dev/null | awk -F: '$1=="fpr"{print $10}')"
_match=0
for _want in "${_want_fprs[@]}"; do
  if grep -qx "${_want}" <<<"${_got_fprs}"; then
    _match=1
  fi
done
if [[ "${_match}" != "1" ]]; then
  echo "error: Docker key fingerprint verification failed (no pinned DOCKER_KEY_FINGERPRINTS match)" >&2
  exit 1
fi
install -o root -g root -m 0644 "${docker_key_tmp}" "${DOCKER_KEYRING}"

# --- Docker APT source (exact pinned content) ---
docker_sources_tmp="$(mktemp)"
cat >"${docker_sources_tmp}" <<SOURCES
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Architectures: arm64
Signed-By: /etc/apt/keyrings/docker.asc
SOURCES
if [[ -e "${DOCKER_SOURCES}" ]] && cmp -s "${docker_sources_tmp}" "${DOCKER_SOURCES}"; then
  :
else
  install -o root -g root -m 0644 "${docker_sources_tmp}" "${DOCKER_SOURCES}"
  apt_changed=1
fi

# --- gVisor APT key (fingerprint pinned) ---
gvisor_key_tmp="$(mktemp)"
curl -fsSL "${GVISOR_ARCHIVE_KEY_URL}" -o "${gvisor_key_tmp}"
gpg --dearmor --output "${gvisor_key_tmp}.dearmored" "${gvisor_key_tmp}"
_gvisor_got="$(gpg --show-keys --with-colons "${gvisor_key_tmp}.dearmored" 2>/dev/null | awk -F: '$1=="fpr"{print $10}')"
if ! grep -qx "${GVISOR_KEY_FINGERPRINT}" <<<"${_gvisor_got}"; then
  echo "error: gVisor key fingerprint verification failed (want ${GVISOR_KEY_FINGERPRINT})" >&2
  exit 1
fi
install -o root -g root -m 0644 "${gvisor_key_tmp}.dearmored" "${GVISOR_KEYRING}"

# --- gVisor APT source (date-specific suite, exact pinned content) ---
# A date suite may receive point updates, so the exact binary release is also
# checked immediately after installation. A different point release is rejected.
gvisor_list_tmp="$(mktemp)"
cat >"${gvisor_list_tmp}" <<GVISORLIST
deb [arch=arm64 signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases ${GVISOR_APT_SUITE} main
GVISORLIST
if [[ -e "${GVISOR_LIST}" ]] && cmp -s "${gvisor_list_tmp}" "${GVISOR_LIST}"; then
  :
else
  install -o root -g root -m 0644 "${gvisor_list_tmp}" "${GVISOR_LIST}"
  apt_changed=1
fi

apt-get update

# --- gVisor package first ---
# On a clean host this keeps gVisor's Docker auto-configuration hook from
# observing an installed Docker CLI/daemon. On an existing reviewed host the
# canonical daemon.json above is already in place and is checked again below.
runsc_matches_pin=0
if [[ -x "${RUNSC_BIN}" ]] && "${RUNSC_BIN}" --version 2>/dev/null | grep -q "release-${GVISOR_RELEASE}"; then
  runsc_matches_pin=1
fi
if ! dpkg-query -W -f='${Status}\n' runsc 2>/dev/null | grep -qx 'install ok installed' \
  || [[ "${runsc_matches_pin}" != "1" ]]; then
  apt-get install --no-install-recommends -y runsc
  apt_changed=1
fi
if [[ ! -x "${RUNSC_BIN}" ]] || ! "${RUNSC_BIN}" --version 2>/dev/null | grep -q "release-${GVISOR_RELEASE}"; then
  echo "error: installed runsc does not match release-${GVISOR_RELEASE}" >&2
  exit 1
fi
if ! daemon_json_matches_pin; then
  echo "error: gVisor package changed ${DOCKER_DAEMON_JSON}; refusing to continue" >&2
  exit 1
fi

# --- Exact Docker package set ---
if dpkg-query -W -f='${Version}\n' docker-ce 2>/dev/null | grep -qx "${DOCKER_CE_VERSION}" \
  && dpkg-query -W -f='${Version}\n' docker-ce-cli 2>/dev/null | grep -qx "${DOCKER_CE_CLI_VERSION}" \
  && dpkg-query -W -f='${Version}\n' docker-ce-rootless-extras 2>/dev/null | grep -qx "${DOCKER_CE_ROOTLESS_EXTRAS_VERSION}" \
  && dpkg-query -W -f='${Version}\n' containerd.io 2>/dev/null | grep -qx "${CONTAINERD_IO_VERSION}" \
  && dpkg-query -W -f='${Version}\n' docker-buildx-plugin 2>/dev/null | grep -qx "${DOCKER_BUILDX_PLUGIN_VERSION}" \
  && dpkg-query -W -f='${Version}\n' docker-compose-plugin 2>/dev/null | grep -qx "${DOCKER_COMPOSE_PLUGIN_VERSION}"; then
  echo "pinned Docker packages already installed"
else
  apt-get install --no-install-recommends -y \
    "docker-ce=${DOCKER_CE_VERSION}" \
    "docker-ce-cli=${DOCKER_CE_CLI_VERSION}" \
    "docker-ce-rootless-extras=${DOCKER_CE_ROOTLESS_EXTRAS_VERSION}" \
    "containerd.io=${CONTAINERD_IO_VERSION}" \
    "docker-buildx-plugin=${DOCKER_BUILDX_PLUGIN_VERSION}" \
    "docker-compose-plugin=${DOCKER_COMPOSE_PLUGIN_VERSION}"
  apt_changed=1
fi
if ! daemon_json_matches_pin; then
  echo "error: package installation changed ${DOCKER_DAEMON_JSON}; refusing to continue" >&2
  exit 1
fi

# --- Services (restart docker only when reviewed state changed) ---
systemctl enable containerd.service
systemctl start containerd.service
systemctl enable docker.service
if [[ "${apt_changed}" == "1" || "${daemon_changed}" == "1" ]]; then
  systemctl restart docker.service
else
  systemctl start docker.service
fi

# --- Post-install firewall re-check (must still satisfy hard invariants) ---
POST_IPTABLES_DOCKER="$(iptables_docker_count)"
POST_IPV4_FORWARD="$(sysctl -n net.ipv4.ip_forward 2>/dev/null || echo unknown)"
POST_IPV6_FORWARD="$(sysctl -n net.ipv6.conf.all.forwarding 2>/dev/null || echo unknown)"
POST_LISTEN_FIRST="$(tcp_listen_count "${DOCKER_TCP_FIRST}")"
POST_LISTEN_SECOND="$(tcp_listen_count "${DOCKER_TCP_SECOND}")"
assert_firewall_invariants "post-install firewall" \
  "${POST_IPTABLES_DOCKER}" "${POST_IPV4_FORWARD}" "${POST_IPV6_FORWARD}" \
  "${POST_LISTEN_FIRST}" "${POST_LISTEN_SECOND}"

# --- Final verification (fail-closed) ---
bash "${VERIFY_SCRIPT}"
echo "container host install complete: result=ok"
