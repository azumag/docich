#!/usr/bin/env python3
"""Read-only verifier for the P5i production canary smoke result.

Production `exec` output is withheld by the VM gateway.  This helper therefore
communicates only through a small exit-code contract and never prints the smoke
JSON, paths, Docker image id, logs, or error details.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from docich.config import load_global  # noqa: E402

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
EPOCH_RE = re.compile(r"^[0-9]{1,12}$")
MAX_RESULT_BYTES = 32 * 1024
EXIT_OK = 0
EXIT_PENDING = 3
EXIT_FAILED = 4
EXIT_UNIT_UNAVAILABLE = 5


def _system_unit_available() -> bool:
    try:
        path = Path("/etc/systemd/system/docich-nethack-canary-smoke.service")
        watcher = Path("/etc/systemd/system/docich-nethack-canary-smoke.path")
        return path.is_file() and watcher.is_file()
    except OSError:
        return False


def verify(expected_sha: str, expected_epoch: str) -> int:
    if SHA_RE.fullmatch(expected_sha) is None or EPOCH_RE.fullmatch(expected_epoch) is None:
        return EXIT_FAILED
    if not _system_unit_available():
        return EXIT_UNIT_UNAVAILABLE
    try:
        g = load_global(ROOT, ROOT / "config" / "docich.soren-live.toml")
        path = Path(g.state_dir) / "nethack" / "production-smoke" / "result.json"
        stat = path.stat()
        if not path.is_file() or stat.st_size <= 0 or stat.st_size > MAX_RESULT_BYTES:
            return EXIT_PENDING
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return EXIT_PENDING
    except Exception:
        return EXIT_FAILED
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        return EXIT_FAILED
    if data.get("sha") != expected_sha or data.get("epoch") != expected_epoch:
        return EXIT_PENDING
    if data.get("status") != "success":
        return EXIT_FAILED
    if (
        data.get("production_untouched") is not True
        or data.get("runsc_required") is not True
        or data.get("arm") != "baseline"
        or data.get("worker_status") != "completed"
        or data.get("policy_effect") != "none"
    ):
        return EXIT_FAILED
    terminal = data.get("terminal_status")
    if terminal not in {"dead", "ascended", "ended", "ended_unknown", "timeout"}:
        return EXIT_FAILED
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sha", required=True)
    parser.add_argument("--epoch", required=True)
    args = parser.parse_args(argv)
    return verify(args.sha, args.epoch)


if __name__ == "__main__":
    raise SystemExit(main())
