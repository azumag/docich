#!/usr/bin/env python3
"""Fixed, installed-only SSH operation for encrypted completed Hanjuku evidence.

No shell, caller paths, remote destinations, diagnostics projection or runtime
code imports. Activation requires the owner to upgrade the root-owned gateway.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import re
import resource
import signal
import stat
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hanjuku_evidence as evidence

OPERATION = "hanjuku_evidence"
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
CONFIG = Path("/etc/azumag-vm-ops.json")
PRODUCTION = Path("/home/ubuntu/docich")
OPERATIONS_STATE = Path("/home/ubuntu/.local/state/github-vm-ops")
INSTALL = Path("/usr/local/libexec/azumag-vm-ops")
TRUSTED_UID = 0
MAX_REQUEST = 24 * 1024
MAX_CIPHERTEXT = evidence.MAX_TOTAL + 1024 * 1024
INSTALLED_FILES = (
    "gateway_entry.py", "gateway.py", "ops_brief.py", "projection_io.py",
    "hanjuku_evidence_gateway.py", "hanjuku_evidence.py",
)


def parse_command(command):
    if not isinstance(command, str) or len(command) > 256:
        raise evidence.EvidenceError("invalid_command")
    parts = command.split(" ")
    if (len(parts) != 4 or parts[:3] != [OPERATION, "docich", "production"]
            or not SHA_RE.fullmatch(parts[3])):
        raise evidence.EvidenceError("invalid_command")
    return parts[3]


def parse_request(raw):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_REQUEST:
        raise evidence.EvidenceError("invalid_request")
    data = evidence._json(raw)
    if (not isinstance(data, dict) or set(data) != {"schema", "runtime_id", "recipient"}
            or type(data["schema"]) is not int or data["schema"] != 1
            or not isinstance(data["runtime_id"], str)
            or not evidence.RUN_ID.fullmatch(data["runtime_id"])
            or not isinstance(data["recipient"], str)):
        raise evidence.EvidenceError("invalid_request")
    try:
        cert = data["recipient"].encode("ascii")
    except UnicodeError:
        raise evidence.EvidenceError("invalid_recipient") from None
    # The existing encryptor validates PEM framing and RSA-OAEP compatibility.
    # This runs before any source-state access.
    evidence.encrypt(b"recipient-validation", cert)
    return data["runtime_id"], cert


def _trusted_read(path, limit):
    with evidence._directory(path.parent) as parent:
        directory = os.fstat(parent)
        meta = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if (directory.st_uid != TRUSTED_UID or directory.st_mode & 0o022
                or meta.st_uid != TRUSTED_UID or meta.st_mode & 0o022):
            raise evidence.EvidenceError("untrusted_installation")
        return evidence._read(parent, path.name, limit)


def load_config(path):
    if Path(path) != CONFIG:
        raise evidence.EvidenceError("invalid_configuration")
    cfg = evidence._json(_trusted_read(CONFIG, 64 * 1024))
    if (not isinstance(cfg, dict) or cfg.get("state") != str(OPERATIONS_STATE)
            or not isinstance(cfg.get("repos"), dict) or set(cfg["repos"]) != {"docich"}
            or not isinstance(cfg["repos"]["docich"], dict)
            or cfg["repos"]["docich"].get("production") != str(PRODUCTION)
            or cfg["repos"]["docich"].get("mode") != "git"):
        raise evidence.EvidenceError("invalid_configuration")
    return cfg


@contextmanager
def operations_lock():
    # Reuse the existing deployment lock, read-only and non-blocking. Never
    # create a new lock or install/repair/bootstrap a missing registration.
    with evidence._directory(OPERATIONS_STATE) as parent:
        fd = os.open("vm-operations.lock", evidence.FLAGS, dir_fd=parent)
        try:
            meta = os.fstat(fd)
            if not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1:
                raise evidence.EvidenceError("unsafe_lock")
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            named = os.stat("vm-operations.lock", dir_fd=parent, follow_symlinks=False)
            if (named.st_dev, named.st_ino) != (meta.st_dev, meta.st_ino):
                raise evidence.EvidenceError("source_changed")
            yield
            named = os.stat("vm-operations.lock", dir_fd=parent, follow_symlinks=False)
            if (named.st_dev, named.st_ino) != (meta.st_dev, meta.st_ino):
                raise evidence.EvidenceError("source_changed")
        finally:
            os.close(fd)


def verify_installed_source(sha):
    # Compare bytes but never execute candidate/repository code. A stale
    # installed exporter must be explicitly upgraded, not silently substituted.
    for name in INSTALLED_FILES:
        installed = _trusted_read(INSTALL / name, 256 * 1024)
        result = subprocess.run(
            ["/usr/bin/git", "-C", str(PRODUCTION), "-c", "core.hooksPath=/dev/null",
             "show", f"{sha}:ops/vm_actions/{name}"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=10, check=False,
            env={"PATH": "/usr/bin:/bin", "LANG": "C", "GIT_OPTIONAL_LOCKS": "0"},
        )
        if (result.returncode != 0 or len(result.stdout) > 256 * 1024
                or hashlib.sha256(result.stdout).digest() != hashlib.sha256(installed).digest()):
            raise evidence.EvidenceError("installed_source_mismatch")


def _ready(core, cfg, sha):
    result = core.status_result(cfg, "docich", "production", sha)
    if result.get("status") != "configured" or result.get("sha") != sha:
        raise evidence.EvidenceError("production_unverified")


def export(core, config_path, command, raw):
    sha = parse_command(command)
    runtime_id, cert = parse_request(raw)
    cfg = load_config(config_path)
    with operations_lock():
        _ready(core, cfg, sha)
        verify_installed_source(sha)
        identity, data, missing = evidence.snapshot(PRODUCTION / "run-soren-live", runtime_id)
        ciphertext = evidence.encrypt(evidence.build_archive(identity, data, missing), cert)
        if not 0 < len(ciphertext) <= MAX_CIPHERTEXT:
            raise evidence.EvidenceError("ciphertext_too_large")
        _ready(core, cfg, sha)
    return ciphertext


def main(core, config_path):
    # Limits apply only to this one exporter process, never to the shared game
    # or the legacy gateway operations. No VMOPS_TESTING/environment bypass.
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        signal.alarm(120)
        os.environ["GIT_OPTIONAL_LOCKS"] = "0"
        os.environ["PATH"] = "/usr/bin:/bin"
        os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
        os.environ["GIT_CONFIG_GLOBAL"] = "/dev/null"
        raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
        ciphertext = export(core, config_path, os.environ.get("SSH_ORIGINAL_COMMAND", ""), raw)
        signal.alarm(0)
    except Exception:
        # Do not write exception details, request bytes, or source evidence to
        # the legacy gateway's private exec/error log either.
        print("Hanjuku evidence export rejected", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(ciphertext)
    sys.stdout.buffer.flush()
    return 0
