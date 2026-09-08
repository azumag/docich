#!/usr/bin/env python3
"""Bounded normalize of owned submodule checkouts other than games/soviet_now.

Only a clean checkout away from its recorded gitlink is moved to that exact
reviewed object. Tracked drift stays fail-closed with a numeric exit code.
The whole file is piped via production exec stdin (gateway cap 16384 bytes).
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
OTHER_SUBMODULES = ("games/hanjuku-sfc-speedrun",)

REASON_INVALID_ROOT = 40
REASON_INVALID_SHA = 42
REASON_OTHER_SUBMODULE_DRIFT = 45
REASON_ROOT_MOVED = 50
REASON_ROOT_DRIFT = 51
REASON_OLD_GITLINK_MISMATCH = 52
REASON_SUBMODULE_MISSING = 53
REASON_MUTATION_FAILED = 59
REASON_POSTVERIFY_FAILED = 60
REASON_GIT_VERIFICATION_FAILED = 61


class NormalizeError(RuntimeError):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def _git(root: Path, *args: str, reason: int = REASON_GIT_VERIFICATION_FAILED) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), "-c", "core.hooksPath=/dev/null", *args],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise NormalizeError(reason, "git verification failed") from exc


def _gitlink_at(root: Path, commit: str, path: str) -> str:
    line = _git(root, "ls-tree", commit, "--", path)
    fields = line.split()
    if len(fields) < 3 or fields[0] != "160000" or fields[1] != "commit" or not SHA_RE.fullmatch(fields[2]):
        raise NormalizeError(REASON_OLD_GITLINK_MISMATCH, "expected other gitlink is missing")
    return fields[2]


def normalize(root: Path, old_parent: str) -> None:
    if not root.is_absolute() or not root.is_dir():
        raise NormalizeError(REASON_INVALID_ROOT, "invalid production root")
    if not SHA_RE.fullmatch(old_parent):
        raise NormalizeError(REASON_INVALID_SHA, "invalid baseline SHA")
    if _git(root, "rev-parse", "HEAD", reason=REASON_ROOT_MOVED) != old_parent:
        raise NormalizeError(REASON_ROOT_MOVED, "production root moved")
    if _git(root, "status", "--porcelain", "--untracked-files=no", "--ignore-submodules=all", reason=REASON_ROOT_DRIFT):
        raise NormalizeError(REASON_ROOT_DRIFT, "tracked production root drift detected")
    for sub_path in OTHER_SUBMODULES:
        want = _gitlink_at(root, old_parent, sub_path)
        sub = root / sub_path
        if not sub.is_dir():
            raise NormalizeError(REASON_SUBMODULE_MISSING, "other owned submodule checkout is missing")
        if _git(sub, "rev-parse", "HEAD") != want:
            if _git(sub, "status", "--porcelain", "--untracked-files=no"):
                raise NormalizeError(REASON_OTHER_SUBMODULE_DRIFT, "other owned submodule has tracked drift")
            try:
                subprocess.run(
                    ["git", "-C", str(sub), "-c", "core.hooksPath=/dev/null", "checkout", "--detach", "--quiet", want],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=True,
                    timeout=60,
                )
            except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
                raise NormalizeError(REASON_MUTATION_FAILED, "other submodule checkout failed") from exc
        if _git(sub, "rev-parse", "HEAD", reason=REASON_POSTVERIFY_FAILED) != want:
            raise NormalizeError(REASON_POSTVERIFY_FAILED, "other submodule postverify failed")
        if _git(sub, "status", "--porcelain", "--untracked-files=no", reason=REASON_POSTVERIFY_FAILED):
            raise NormalizeError(REASON_POSTVERIFY_FAILED, "other submodule postverify failed")


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        raise NormalizeError(REASON_INVALID_SHA, "usage: normalize ROOT OLD_PARENT")
    normalize(Path(argv[1]), argv[2])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except NormalizeError as exc:
        print(f"other submodule normalize refused: {exc}", file=sys.stderr)
        raise SystemExit(exc.code)
