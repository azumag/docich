#!/usr/bin/env python3
"""Normalize one reviewed pre-synced owned submodule before a production deploy.

Exit codes are intentionally stable and non-sensitive so the owner-only
workflow can distinguish a refused recovery without exposing paths, command
output, file contents, or secrets from the VM.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
ALLOWED_SUBMODULES = {"games/soviet_now"}

REASON_INVALID_ROOT = 40
REASON_UNAPPROVED_SUBMODULE = 41
REASON_INVALID_SHA = 42
REASON_NO_ADVANCE = 43
REASON_ROOT_MOVED = 50
REASON_ROOT_DRIFT = 51
REASON_OLD_GITLINK_MISMATCH = 52
REASON_SUBMODULE_MISSING = 53
REASON_SUBMODULE_HEAD_MISMATCH = 54
REASON_SUBMODULE_DRIFT = 55
REASON_OLD_OBJECT_MISSING = 56
REASON_NEW_OBJECT_MISSING = 57
REASON_NOT_DESCENDANT = 58
REASON_MUTATION_FAILED = 59
REASON_POSTVERIFY_FAILED = 60
REASON_GIT_VERIFICATION_FAILED = 61


class ReconcileError(RuntimeError):
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
        raise ReconcileError(reason, "git verification failed") from exc


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
        raise ReconcileError(REASON_MUTATION_FAILED, "git mutation failed") from exc


def _is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    try:
        subprocess.run(
            ["git", "-C", str(root), "-c", "core.hooksPath=/dev/null", "merge-base", "--is-ancestor", ancestor, descendant],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=30,
        )
        return True
    except subprocess.CalledProcessError as exc:
        if exc.returncode == 1:
            return False
        raise ReconcileError(REASON_GIT_VERIFICATION_FAILED, "git ancestry verification failed") from exc
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise ReconcileError(REASON_GIT_VERIFICATION_FAILED, "git ancestry verification failed") from exc


def _gitlink_at(root: Path, commit: str, path: str) -> str:
    line = _git(root, "ls-tree", commit, "--", path)
    fields = line.split()
    if len(fields) < 3 or fields[0] != "160000" or fields[1] != "commit" or not SHA_RE.fullmatch(fields[2]):
        raise ReconcileError(REASON_OLD_GITLINK_MISMATCH, "expected owned submodule gitlink is missing")
    return fields[2]


def reconcile(root: Path, old_parent: str, old_sub: str, new_sub: str, sub_path: str) -> None:
    if not root.is_absolute() or not root.is_dir():
        raise ReconcileError(REASON_INVALID_ROOT, "invalid production root")
    if sub_path not in ALLOWED_SUBMODULES:
        raise ReconcileError(REASON_UNAPPROVED_SUBMODULE, "submodule is not eligible for automatic reconcile")
    if not all(SHA_RE.fullmatch(value) for value in (old_parent, old_sub, new_sub)):
        raise ReconcileError(REASON_INVALID_SHA, "invalid reconcile SHA")
    if old_sub == new_sub:
        raise ReconcileError(REASON_NO_ADVANCE, "submodule did not advance")

    if _git(root, "rev-parse", "HEAD", reason=REASON_ROOT_MOVED) != old_parent:
        raise ReconcileError(REASON_ROOT_MOVED, "production root moved")
    if _git(root, "status", "--porcelain", "--untracked-files=no", "--ignore-submodules=all", reason=REASON_ROOT_DRIFT):
        raise ReconcileError(REASON_ROOT_DRIFT, "tracked production root drift detected")
    if _gitlink_at(root, old_parent, sub_path) != old_sub:
        raise ReconcileError(REASON_OLD_GITLINK_MISMATCH, "old gitlink no longer matches")

    sub = root / sub_path
    if not sub.is_dir():
        raise ReconcileError(REASON_SUBMODULE_MISSING, "owned submodule checkout is missing")

    current_sub = _git(sub, "rev-parse", "HEAD", reason=REASON_SUBMODULE_HEAD_MISMATCH)
    if current_sub != new_sub:
        # GitHub's reviewed merge commit can have the exact same tree as its
        # PR head while using a different commit SHA. A pre-sync may therefore
        # leave the clean PR head checked out. Accept that one bounded case
        # only when the current commit is an ancestor of the reviewed target
        # and every tracked path/mode is identical via the Git tree object.
        current_tree = _git(sub, "rev-parse", f"{current_sub}^{{tree}}", reason=REASON_SUBMODULE_HEAD_MISMATCH)
        reviewed_tree = _git(sub, "rev-parse", f"{new_sub}^{{tree}}", reason=REASON_NEW_OBJECT_MISSING)
        if current_tree != reviewed_tree or not _is_ancestor(sub, current_sub, new_sub):
            raise ReconcileError(REASON_SUBMODULE_HEAD_MISMATCH, "owned submodule is not at reviewed target")

    if _git(sub, "status", "--porcelain", "--untracked-files=no", reason=REASON_SUBMODULE_DRIFT):
        raise ReconcileError(REASON_SUBMODULE_DRIFT, "owned submodule has tracked drift")

    # Both objects must already be present locally. No network fetch is
    # permitted in this recovery path.
    _git(sub, "cat-file", "-e", f"{old_sub}^{{commit}}", reason=REASON_OLD_OBJECT_MISSING)
    _git(sub, "cat-file", "-e", f"{new_sub}^{{commit}}", reason=REASON_NEW_OBJECT_MISSING)
    if not _is_ancestor(sub, old_sub, new_sub):
        raise ReconcileError(REASON_NOT_DESCENDANT, "reviewed target is not a descendant of the recorded gitlink")

    _git_run(sub, "checkout", "--detach", "--quiet", old_sub)

    if _git(sub, "rev-parse", "HEAD", reason=REASON_POSTVERIFY_FAILED) != old_sub:
        raise ReconcileError(REASON_POSTVERIFY_FAILED, "submodule normalization verification failed")
    if _git(sub, "status", "--porcelain", "--untracked-files=no", reason=REASON_POSTVERIFY_FAILED):
        raise ReconcileError(REASON_POSTVERIFY_FAILED, "submodule normalization verification failed")


def main(argv: list[str]) -> int:
    if len(argv) != 6:
        raise ReconcileError(REASON_INVALID_SHA, "usage: reconcile ROOT OLD_PARENT OLD_SUB NEW_SUB SUB_PATH")
    reconcile(Path(argv[1]), argv[2], argv[3], argv[4], argv[5])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except ReconcileError as exc:
        # The human-readable detail is written only to the VM-side private
        # operation log. The workflow sees the stable numeric exit code.
        print(f"presynced submodule reconcile refused: {exc}", file=sys.stderr)
        raise SystemExit(exc.code)
