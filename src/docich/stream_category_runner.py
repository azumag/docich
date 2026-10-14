"""Serialize detached stream-category updates before executing the Soren hook."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        separator = args.index("--")
        lock_path = Path(args[0])
        command = args[separator + 1 :]
    except (ValueError, IndexError):
        return 2
    if separator != 1 or not command:
        return 2

    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
        import fcntl

        with lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            return subprocess.run(command, check=False).returncode
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
