#!/usr/bin/env python3
"""Dispatch one fixed five-minute Soren91 manual probe as a detached runner."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import time

ROOT = Path("/home/ubuntu/docich")
CONFIG = ROOT / "config/docich.soren-live.toml"
LOG = Path("/home/ubuntu/soren/tmp/logs/corner_manual_soren91_probe.log")


def command(root: Path = ROOT) -> list[str]:
    return [
        str(root / "bin/docich-soren91-corner-manual"),
        "--config",
        str(root / "config/docich.soren-live.toml"),
        "start",
        "--duration-minutes",
        "5",
    ]


def launch(*, root: Path = ROOT, log_path: Path = LOG,
           popen=subprocess.Popen, sleep=time.sleep) -> None:
    if Path.cwd().resolve() != root.resolve():
        raise RuntimeError("fixed_production_root_required")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log_file:
        proc = popen(
            command(root),
            cwd=str(root),
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    sleep(1.0)
    returncode = proc.poll()
    # With common rotation enabled, a successful manual request may be durably
    # queued and the CLI can exit 0 immediately. Only a non-zero early exit is
    # a dispatch failure; an exit-0 request is already owned by rotation state.
    if returncode is not None and returncode != 0:
        raise RuntimeError("manual_probe_exited_with_error")


def main() -> int:
    launch()
    print(json.dumps({"status": "dispatched", "duration_minutes": 5}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print(json.dumps({"status": "failed", "reason": "manual_probe_start_failed"}, sort_keys=True))
        raise SystemExit(1) from None
