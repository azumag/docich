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
        "9DC858229FC7DD38854AE2D88D81803C0EBFCD88",
        "D3306A018370199E527AE7997EA0A9C3F273FCD8",
        "6F1DF85E3A71C24918E727D56FC6D554E32BD943",
    ):
        assert pinned in text


def test_installer_guards_platform_and_firewall_and_daemon_json():
    text = _lower(INSTALL)
    assert "ubuntu" in text and "24.04" in text
    assert "arm64" in text
    assert "baseline" in text
    assert "docker" in text
    assert "ip_forward" in text
    assert "2375" in text
    assert "gpg --show-keys --with-colons" in _read(INSTALL)
    assert "fingerprint" in text
    assert "force_daemon_json" in text


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
    lowered = _lower(VERIFY)
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


def test_provisioning_doc_links_pins_scripts_and_worker_doc():
    assert PROVISION_DOC.is_file()
    text = _lower(PROVISION_DOC)
    assert "5:29.8.0-1~ubuntu.24.04~noble" in text
    assert "ops/container_host/install_container_host.sh" in text
    assert "ops/container_host/verify_container_host.sh" in text
    worker = _lower(WORKER_DOC)
    assert "container-host-provisioning.md" in worker
