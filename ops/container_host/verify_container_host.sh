#!/usr/bin/env bash
# Read-only verifier for the reviewed container-runtime host state.
# Usage: ops/container_host/verify_container_host.sh
# Prints stable `key=value` lines, finishes with `result=ok` or `result=drift`.
# Exit 0 only when every check passes, non-zero on any drift.
# Never modifies the host. Uses `sudo -n` for privileged reads when not root.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/pins.env"

FAIL=0
report() {
  # report <key> <value> [ok|drift]
  local key="$1" value="$2" status="${3:-ok}"
  echo "${key}=${value}"
  if [[ "${status}" != "ok" ]]; then
    FAIL=1
  fi
}

PRIV=""
if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  PRIV="sudo -n"
fi

# --- os=ubuntu-24.04-arm64 ---
os_id="$(grep -E '^ID=' /etc/os-release 2>/dev/null | cut -d= -f2 | tr -d '"' || echo unknown)"
os_ver="$(grep -E '^VERSION_ID=' /etc/os-release 2>/dev/null | cut -d= -f2 | tr -d '"' || echo unknown)"
arch="$(dpkg --print-architecture 2>/dev/null || echo unknown)"
if [[ "${os_id}" == "ubuntu" && "${os_ver}" == "24.04" && "${arch}" == "arm64" ]]; then
  report "os" "ubuntu-24.04-arm64"
else
  report "os" "${os_id}-${os_ver}-${arch}" "drift"
fi

# --- pinned package versions ---
pkg_version() {
  dpkg-query -W -f='${Version}\n' "$1" 2>/dev/null || echo "missing"
}
check_pin() {
  local key="$1" pkg="$2" want="$3"
  local got
  got="$(pkg_version "${pkg}")"
  if [[ "${got}" == "${want}" ]]; then
    report "${key}" "${got}"
  else
    report "${key}" "${got} (want ${want})" "drift"
  fi
}
check_pin "docker_ce_version" "docker-ce" "${DOCKER_CE_VERSION}"
check_pin "docker_ce_cli_version" "docker-ce-cli" "${DOCKER_CE_CLI_VERSION}"
check_pin "containerd_io_version" "containerd.io" "${CONTAINERD_IO_VERSION}"
check_pin "docker_buildx_version" "docker-buildx-plugin" "${DOCKER_BUILDX_PLUGIN_VERSION}"
check_pin "docker_compose_version" "docker-compose-plugin" "${DOCKER_COMPOSE_PLUGIN_VERSION}"
runsc_pkg="$(pkg_version runsc)"
if [[ "${runsc_pkg}" == "missing" ]]; then
  report "runsc_package_version" "missing" "drift"
else
  report "runsc_package_version" "${runsc_pkg}"
fi

# --- docker info capability checks ---
if command -v docker >/dev/null 2>&1; then
  # The operator account is intentionally NOT in the docker group, so privileged
  # reads go through ${PRIV} (empty for root, `sudo -n` otherwise). Querying the
  # CLI directly here would report false drift on the reviewed production host.
  ostype="$(${PRIV} docker info --format '{{.OSType}}' 2>/dev/null || echo unknown)"
  [[ "${ostype}" == "linux" ]] && report "docker_ostype" "${ostype}" || report "docker_ostype" "${ostype}" "drift"
  cgver="$(${PRIV} docker info --format '{{.CgroupVersion}}' 2>/dev/null || echo unknown)"
  [[ "${cgver}" == "2" ]] && report "docker_cgroup_version" "${cgver}" || report "docker_cgroup_version" "${cgver}" "drift"
  defrt="$(${PRIV} docker info --format '{{.DefaultRuntime}}' 2>/dev/null || echo unknown)"
  [[ "${defrt}" == "runc" ]] && report "docker_default_runtime" "${defrt}" || report "docker_default_runtime" "${defrt}" "drift"
  if ${PRIV} docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q "runsc"; then
    report "docker_runsc" "registered"
  else
    report "docker_runsc" "missing" "drift"
  fi
  mem="$(${PRIV} docker info --format '{{.MemoryLimit}}' 2>/dev/null || echo unknown)"
  [[ "${mem}" == "true" ]] && report "docker_memory_limit" "${mem}" || report "docker_memory_limit" "${mem}" "drift"
  pids="$(${PRIV} docker info --format '{{.PidsLimit}}' 2>/dev/null || echo unknown)"
  [[ "${pids}" == "true" ]] && report "docker_pids_limit" "${pids}" || report "docker_pids_limit" "${pids}" "drift"
  quota="$(${PRIV} docker info --format '{{.CPUCfsQuota}}' 2>/dev/null || echo unknown)"
  [[ "${quota}" == "true" ]] && report "docker_cpu_quota" "${quota}" || report "docker_cpu_quota" "${quota}" "drift"
else
  report "docker_ostype" "docker-missing" "drift"
  report "docker_cgroup_version" "docker-missing" "drift"
  report "docker_default_runtime" "docker-missing" "drift"
  report "docker_runsc" "docker-missing" "drift"
  report "docker_memory_limit" "docker-missing" "drift"
  report "docker_pids_limit" "docker-missing" "drift"
  report "docker_cpu_quota" "docker-missing" "drift"
fi

# --- runsc version ---
if [[ -x "${RUNSC_BIN}" ]]; then
  runsc_out="$("${RUNSC_BIN}" --version 2>/dev/null || echo unknown)"
  if grep -q "release-${GVISOR_RELEASE}" <<<"${runsc_out}"; then
    report "runsc_version" "${GVISOR_RELEASE}"
  else
    report "runsc_version" "$(head -n1 <<<"${runsc_out}" | tr -d '\n')" "drift"
  fi
else
  report "runsc_version" "missing" "drift"
fi

# --- daemon.json semantic equality with the pin ---
if [[ -f "${DOCKER_DAEMON_JSON}" ]]; then
  if python3 - "${DOCKER_DAEMON_JSON}" <<'PYEOF' >/dev/null 2>&1
import json, sys
pinned = {"ip-forward": False, "ip6tables": False, "iptables": False,
          "runtimes": {"runsc": {"path": "/usr/bin/runsc"}}}
with open(sys.argv[1], encoding="utf-8") as fh:
    current = json.load(fh)
raise SystemExit(0 if current == pinned else 1)
PYEOF
  then
    report "daemon_json" "canonical"
  else
    report "daemon_json" "drift" "drift"
  fi
else
  report "daemon_json" "missing" "drift"
fi

# --- APT source exact content ---
docker_sources_want="$(printf 'Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: noble\nComponents: stable\nArchitectures: arm64\nSigned-By: /etc/apt/keyrings/docker.asc\n')"
if [[ -f "${DOCKER_SOURCES}" ]] && [[ "$(cat "${DOCKER_SOURCES}")" == "${docker_sources_want}" ]]; then
  report "docker_sources" "canonical"
else
  report "docker_sources" "drift" "drift"
fi
gvisor_list_want="deb [arch=arm64 signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases ${GVISOR_APT_SUITE} main"
if [[ -f "${GVISOR_LIST}" ]] && [[ "$(cat "${GVISOR_LIST}")" == "${gvisor_list_want}" ]]; then
  report "gvisor_list" "canonical"
else
  report "gvisor_list" "drift" "drift"
fi

# --- firewall invariants: zero DOCKER rules, forwarding 0/0, no 2375/2376 listeners ---
packet_filter=""
packet_filter_ok=0
if command -v iptables >/dev/null 2>&1; then
  if packet_filter="$(${PRIV} iptables -S 2>/dev/null)"; then
    packet_filter_ok=1
  fi
elif command -v iptables-save >/dev/null 2>&1; then
  if packet_filter="$(${PRIV} iptables-save 2>/dev/null)"; then
    packet_filter_ok=1
  fi
fi
if [[ "${packet_filter_ok}" == "1" ]]; then
  n="$(grep -c DOCKER <<<"${packet_filter}" || true)"
  if [[ "${n}" == "0" ]]; then
    report "iptables_docker_rules" "0"
  else
    report "iptables_docker_rules" "${n}" "drift"
  fi
else
  report "iptables_docker_rules" "unavailable" "drift"
fi

v4="$(${PRIV} sysctl -n net.ipv4.ip_forward 2>/dev/null || echo unknown)"
[[ "${v4}" == "0" ]] && report "ipv4_forwarding" "${v4}" || report "ipv4_forwarding" "${v4} (net.ipv4.ip_forward)" "drift"
v6="$(${PRIV} sysctl -n net.ipv6.conf.all.forwarding 2>/dev/null || echo unknown)"
[[ "${v6}" == "0" ]] && report "ipv6_forwarding" "${v6}" || report "ipv6_forwarding" "${v6} (net.ipv6.conf.all.forwarding)" "drift"

listeners=""
listener_check_ok=0
if command -v ss >/dev/null 2>&1; then
  if listeners="$(ss -ltn 2>/dev/null)"; then
    listener_check_ok=1
  fi
elif command -v netstat >/dev/null 2>&1; then
  if listeners="$(netstat -ltn 2>/dev/null)"; then
    listener_check_ok=1
  fi
fi
if [[ "${listener_check_ok}" != "1" ]]; then
  report "listen_2375" "unavailable" "drift"
  report "listen_2376" "unavailable" "drift"
elif grep -Eq ':(2375|2376)\b' <<<"${listeners}"; then
  grep -Eq ':2375\b' <<<"${listeners}" && report "listen_2375" "present" "drift" || report "listen_2375" "none"
  grep -Eq ':2376\b' <<<"${listeners}" && report "listen_2376" "present" "drift" || report "listen_2376" "none"
else
  report "listen_2375" "none"
  report "listen_2376" "none"
fi

# --- docker group membership: ubuntu must NOT be in the docker group ---
if id -nG ubuntu 2>/dev/null | tr ' ' '\n' | grep -qx docker; then
  report "ubuntu_in_docker_group" "true" "drift"
else
  report "ubuntu_in_docker_group" "false"
fi

if [[ "${FAIL}" == "0" ]]; then
  echo "result=ok"
  exit 0
else
  echo "result=drift"
  exit 1
fi
