import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "ops" / "container_host" / "install_container_host.sh"
VERIFY = ROOT / "ops" / "container_host" / "verify_container_host.sh"
PINS = ROOT / "ops" / "container_host" / "pins.env"
PROVISION_DOC = ROOT / "docs" / "operations" / "container-host-provisioning.md"
WORKER_DOC = ROOT / "docs" / "operations" / "free-strategy-paper-worker.md"


def _read(path):
    return Path(path).read_text(encoding="utf-8")


def _lower(path):
    return _read(path).lower()


def test_scripts_pass_bash_syntax_check():
    for script in (INSTALL, VERIFY):
        proc = subprocess.run(
            ["bash", "-n", str(script)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr


def test_scripts_have_shebang_and_strict_mode():
    for script in (INSTALL, VERIFY):
        text = _read(script)
        first = text.splitlines()[0].strip()
        assert first == "#!/usr/bin/env bash"
        assert "set -euo pipefail" in text


def test_pins_contain_reviewed_versions_and_fingerprints():
    text = _read(PINS)
    for pinned in (
        "DOCKER_CE_VERSION=5:29.8.0-1~ubuntu.24.04~noble",
        "DOCKER_CE_CLI_VERSION=5:29.8.0-1~ubuntu.24.04~noble",
        "DOCKER_CE_ROOTLESS_EXTRAS_VERSION=5:29.8.0-1~ubuntu.24.04~noble",
        "CONTAINERD_IO_VERSION=2.3.5-1~ubuntu.24.04~noble",
        "DOCKER_BUILDX_PLUGIN_VERSION=0.37.1-1~ubuntu.24.04~noble",
        "DOCKER_COMPOSE_PLUGIN_VERSION=5.5.1-1~ubuntu.24.04~noble",
        "GVISOR_RELEASE=20260907.0",
        "GVISOR_APT_SUITE=20260907",
        "9DC858229FC7DD38854AE2D88D81803C0EBFCD88",
        "D3306A018370199E527AE7997EA0A9C3F273FCD8",
        "6F1DF85E3A71C24918E727D56FC6D554E32BD943",
    ):
        assert pinned in text


def test_installer_guards_platform_firewall_daemon_and_gvisor_runtime():
    text = _read(INSTALL)
    lowered = text.lower()
    assert "ubuntu" in lowered and "24.04" in lowered
    assert "arm64" in lowered
    assert "baseline" in lowered
    assert "docker" in lowered
    assert "ip_forward" in lowered
    assert "2375" in lowered
    assert "gpg --show-keys --with-colons" in text
    assert "fingerprint" in lowered
    assert "force_daemon_json" in lowered
    assert "${GVISOR_APT_SUITE} main" in text
    assert '"docker-compose-plugin=${DOCKER_COMPOSE_PLUGIN_VERSION}"' in text
    assert "    runsc\n" in text
    assert 'grep -q "release-${GVISOR_RELEASE}"' in text


def test_installer_fails_closed_when_security_inspection_is_unavailable():
    text = _read(INSTALL)
    assert "iptables inspection is required" in text
    assert "socket-listener inspection is required" in text
    # Security checks must never interpret an absent inspection tool as zero rules/listeners.
    assert "elif command -v iptables-save" in text
    assert "else\n    echo 0\n  fi" not in text


def test_installer_forbids_unsafe_operations():
    text = _lower(INSTALL)
    for forbidden in (
        "usermod",
        "iptables -a",
        "nft ",
        "ufw ",
        "--publish",
        "-p 2375",
        "2376",
        "--network host",
        "--network=host",
        "dockerd-rootless",
    ):
        assert forbidden not in text, forbidden


def test_verifier_covers_reviewed_invariants_and_reports_result():
    text = _read(VERIFY)
    lowered = text.lower()
    for expected in (
        "ip_forward",
        "runsc",
        "daemon.json",
        "docker",
        "2375",
        "2376",
        "group",
        "result=",
    ):
        assert expected in lowered, expected
    assert "runsc_package_version" in text
    assert "${GVISOR_APT_SUITE} main" in text
    assert 'report "iptables_docker_rules" "unavailable" "drift"' in text
    assert 'report "listen_2375" "unavailable" "drift"' in text
    assert 'report "listen_2376" "unavailable" "drift"' in text


def test_provisioning_doc_links_pins_scripts_and_worker_doc():
    assert PROVISION_DOC.is_file()
    text = _lower(PROVISION_DOC)
    assert "5:29.8.0-1~ubuntu.24.04~noble" in text
    assert "20260907" in text
    assert "runsc" in text and "apt" in text
    assert "ops/container_host/install_container_host.sh" in text
    assert "ops/container_host/verify_container_host.sh" in text
    worker = _lower(WORKER_DOC)
    assert "container-host-provisioning.md" in worker
