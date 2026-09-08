#!/usr/bin/env python3
"""Normalize one reviewed pre-synced owned submodule before a production deploy.

This helper is intentionally narrow.  It only moves the *docich worktree's*
owned submodule checkout back to the gitlink recorded by the current root
commit when all of the following are proven:

* the production root is exactly the expected old parent and has no tracked
  root drift (ignoring submodule HEAD movement),
* the old parent records the expected old gitlink,
* the submodule checkout is clean and is exactly at the reviewed new gitlink,
* the old gitlink is an ancestor of the reviewed new gitlink.

It never writes the live projection (for example /home/ubuntu/soren), never
changes deployment state, and never fetches from the network.  The normal VM
gateway deploy remains responsible for projection verification/adoption and
for the canonical state transition.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
ALLOWED_SUBMODULES = {"games/soviet_now"}


class ReconcileError(RuntimeError):
    pass


def _git(root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), "-c", "core.hooksPath=/dev/null", *args],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise ReconcileError("git verification failed") from exc


def _git_run(root: Path, *args: str) -> None:
    try:
        subprocess.run(
            ["git", "-C", str(root), "-c", "core.hooksPath=/dev/null", *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=60,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise ReconcileError("git mutation failed") from exc


def _gitlink_at(root: Path, commit: str, path: str) -> str:
    line = _git(root, "ls-tree", commit, "--", path)
    fields = line.split()
    if len(fields) < 3 or fields[0] != "160000" or fields[1] != "commit" or not SHA_RE.fullmatch(fields[2]):
        raise ReconcileError("expected owned submodule gitlink is missing")
    return fields[2]


def reconcile(root: Path, old_parent: str, old_sub: str, new_sub: str, sub_path: str) -> None:
    if not root.is_absolute() or not root.is_dir():
        raise ReconcileError("invalid production root")
    if sub_path not in ALLOWED_SUBMODULES:
        raise ReconcileError("submodule is not eligible for automatic reconcile")
    if not all(SHA_RE.fullmatch(value) for value in (old_parent, old_sub, new_sub)):
        raise ReconcileError("invalid reconcile SHA")
    if old_sub == new_sub:
        raise ReconcileError("submodule did not advance")

    if _git(root, "rev-parse", "HEAD") != old_parent:
        raise ReconcileError("production root moved")
    if _git(root, "status", "--porcelain", "--untracked-files=no", "--ignore-submodules=all"):
        raise ReconcileError("tracked production root drift detected")
    if _gitlink_at(root, old_parent, sub_path) != old_sub:
        raise ReconcileError("old gitlink no longer matches")

    sub = root / sub_path
    if not sub.is_dir():
        raise ReconcileError("owned submodule checkout is missing")
    if _git(sub, "rev-parse", "HEAD") != new_sub:
        raise ReconcileError("owned submodule is not at reviewed target")
    if _git(sub, "status", "--porcelain", "--untracked-files=no"):
        raise ReconcileError("owned submodule has tracked drift")

    # Both objects must already be present locally.  No network fetch is
    # permitted in this recovery path.
    _git(sub, "cat-file", "-e", f"{old_sub}^{{commit}}")
    _git(sub, "cat-file", "-e", f"{new_sub}^{{commit}}")
    try:
        subprocess.run(
            ["git", "-C", str(sub), "-c", "core.hooksPath=/dev/null", "merge-base", "--is-ancestor", old_sub, new_sub],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=30,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise ReconcileError("reviewed target is not a descendant of the recorded gitlink") from exc

    _git_run(sub, "checkout", "--detach", "--quiet", old_sub)

    if _git(sub, "rev-parse", "HEAD") != old_sub or _git(sub, "status", "--porcelain", "--untracked-files=no"):
        raise ReconcileError("submodule normalization verification failed")


def main(argv: list[str]) -> int:
    if len(argv) != 6:
        raise ReconcileError("usage: reconcile ROOT OLD_PARENT OLD_SUB NEW_SUB SUB_PATH")
    reconcile(Path(argv[1]), argv[2], argv[3], argv[4], argv[5])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except ReconcileError as exc:
        print(f"presynced submodule reconcile refused: {exc}", file=sys.stderr)
        raise SystemExit(1)
