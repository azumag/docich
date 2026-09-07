#!/usr/bin/env python3
"""Apply the reviewed Soren supervisor SIGPIPE fix to the exact live preimage."""
from __future__ import annotations

import hashlib
import os
import stat
import sys
import tempfile
from pathlib import Path

TARGET = Path("/home/ubuntu/soren/start_all.sh")
OLD_SHA256 = "ae87c7e2e47be74cfdd5f8fcc1a7749a747b95a75ee5731468e8a67681616830"
NEW_SHA256 = "90580eee5d5c21708b6314a026570a4281617bffb63af633a95424738b155af7"
SOURCE_COMMIT = "4be20e3cd4e1028f9e034d1e4e7b9b02907cf481"

OLD_FRAGMENT = '''\tif matches=$(pgrep -f "$pattern" 2>/dev/null); then
\t\tprintf '%s\\n' "$matches" | grep -qx "$pid"
\t\treturn $?
\tfi
'''
NEW_FRAGMENT = '''\tif matches=$(pgrep -f "$pattern" 2>/dev/null); then
\t\t# Avoid `printf | grep -q` here. With pipefail enabled, grep can exit
\t\t# after the target PID while printf still has additional pgrep hits,
\t\t# turning a valid match into rc=141 (SIGPIPE) and a false stale-worker
\t\t# adoption. Iterate the captured lines in-process instead.
\t\twhile IFS= read -r match; do
\t\t\t[ "$match" = "$pid" ] && return 0
\t\tdone <<<"$matches"
\t\treturn 1
\tfi
'''


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def transform(text: str) -> str:
    if text.count(OLD_FRAGMENT) != 1:
        raise ValueError("reviewed hotfix preimage fragment mismatch")
    return text.replace(OLD_FRAGMENT, NEW_FRAGMENT, 1)


def apply(path: Path = TARGET) -> str:
    raw = path.read_bytes()
    current = sha256(raw)
    if current == NEW_SHA256:
        return "already_applied"
    if current != OLD_SHA256:
        raise ValueError(f"refusing unreviewed live drift: sha256={current}")
    updated = transform(raw.decode("utf-8")).encode("utf-8")
    if sha256(updated) != NEW_SHA256:
        raise ValueError("hotfix output hash does not match reviewed production-preserving transform")
    st = path.stat()
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.hotfix-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(updated)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_name, stat.S_IMODE(st.st_mode))
        os.replace(tmp_name, path)
        dir_fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
    if sha256(path.read_bytes()) != NEW_SHA256:
        raise RuntimeError("post-replace verification failed")
    return "applied"


def main() -> int:
    if len(sys.argv) != 1:
        print("no arguments accepted", file=sys.stderr)
        return 2
    try:
        result = apply()
    except Exception as exc:
        print(f"hotfix refused: {exc}", file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
