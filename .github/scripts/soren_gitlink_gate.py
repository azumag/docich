#!/usr/bin/env python3
"""Fail-open CI gate for a changed games/soviet_now gitlink.

If a directly relevant docich path changed, print true. Otherwise, when only the
Soren gitlink is relevant, fetch the two reviewed Soren commits and test their
internal changed paths against the supplied patterns. Any ambiguity/failure
prints true so CI coverage is never silently reduced.
"""
from __future__ import annotations

import argparse
import fnmatch
import os
from pathlib import Path
import subprocess
import tempfile


def run(*args: str, cwd: Path | None = None) -> str:
    return subprocess.check_output(args, cwd=cwd, text=True, stderr=subprocess.DEVNULL).strip()


def matches(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def sub_sha(root: str) -> str:
    line = run("git", "ls-tree", root, "--", "games/soviet_now")
    fields = line.split()
    if len(fields) < 3 or fields[0] != "160000" or fields[1] != "commit":
        raise RuntimeError("invalid gitlink")
    return fields[2]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--before", required=True)
    p.add_argument("--after", required=True)
    p.add_argument("--root-path", action="append", default=[])
    p.add_argument("--soren-path", action="append", default=[])
    args = p.parse_args()

    try:
        if not args.before or not args.after or set(args.before) == {"0"}:
            print("true")
            return 0
        changed = run("git", "diff", "--name-only", args.before, args.after).splitlines()
        direct = [path for path in changed if path != "games/soviet_now"]
        if any(matches(path, args.root_path) for path in direct):
            print("true")
            return 0
        if "games/soviet_now" not in changed:
            print("false")
            return 0

        old, new = sub_sha(args.before), sub_sha(args.after)
        if old == new:
            print("false")
            return 0
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run("git", "init", "-q", cwd=root)
            remote = os.environ.get("SOREN_GITLINK_GATE_REMOTE", "https://github.com/azumag/soviet_now.git")
            run("git", "remote", "add", "origin", remote, cwd=root)
            run("git", "fetch", "-q", "--no-tags", "--depth=1", "origin", old, new, cwd=root)
            internal = run("git", "diff", "--name-only", old, new, cwd=root).splitlines()
        print("true" if any(matches(path, args.soren_path) for path in internal) else "false")
        return 0
    except Exception:
        print("true")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
