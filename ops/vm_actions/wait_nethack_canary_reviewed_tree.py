#!/usr/bin/env python3
"""Wait until the deployed P5i smoke surface matches one reviewed HEAD.

A systemd PathChanged event can arrive while git is still replacing files.
This preflight waits for a stable HEAD plus a clean security-relevant path set,
including the epoch trigger itself, before the Docker-capable oneshot starts.
"""
from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
PATHS = (
    "ops/vm_actions/nethack_canary_smoke_epoch",
    "ops/vm_actions/nethack_canary_production_smoke.py",
    "ops/vm_actions/wait_nethack_canary_reviewed_tree.py",
    "src/docich/nethack_canary.py",
    "src/docich/nethack_canary_container.py",
    "src/docich/nethack_canary_worker.py",
    "src/docich/nethack_canary_executor.py",
    "containers/nethack-canary/Dockerfile",
    "brains",
)


def _sample() -> str | None:
    try:
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if SHA_RE.fullmatch(head) is None:
        return None
    clean = subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", *PATHS],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
        check=False,
    )
    cached = subprocess.run(
        ["git", "diff", "--cached", "--quiet", "HEAD", "--", *PATHS],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
        check=False,
    )
    return head if clean.returncode == 0 and cached.returncode == 0 else None


def main() -> int:
    deadline = time.monotonic() + 120.0
    previous: str | None = None
    stable = 0
    while time.monotonic() < deadline:
        try:
            head = _sample()
        except subprocess.TimeoutExpired:
            head = None
        if head is not None and head == previous:
            stable += 1
            if stable >= 2:
                return 0
        else:
            previous = head
            stable = 0
        time.sleep(2.0)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
