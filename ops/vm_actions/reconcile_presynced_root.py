#!/usr/bin/env python3
"""Bounded reconciliation for reviewed-ahead parent worktree drift.

This helper is intentionally narrow.  It never resets the repository and never
adopts unknown bytes.  A dirty tracked regular file is restored to the recorded
old commit only when its *current* bytes+mode exactly match that same path in an
actual commit on the reviewed old..new main lineage.  Staged changes, symlinks,
untracked files, unknown bytes, excessive scans, and concurrent changes all
fail closed.
"""

import hashlib
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
MAX_DIRTY_PATHS = 64
MAX_LINEAGE_COMMITS = 96
MAX_FILE_BYTES = 32 * 1024 * 1024

REASON_INVALID_ROOT = 40
REASON_INVALID_SHA = 42
REASON_ROOT_MOVED = 50
REASON_NOT_DESCENDANT = 58
REASON_GIT_VERIFICATION_FAILED = 61
REASON_UNSUPPORTED_PATH = 62
REASON_CONCURRENT_DRIFT = 63
REASON_STAGED_DRIFT = 66
REASON_SCAN_BOUND = 67
REASON_UNKNOWN_DRIFT = 68
REASON_MUTATION_FAILED = 69
REASON_POSTVERIFY_FAILED = 70


class ReconcileError(RuntimeError):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def _gb(root: Path):
    return ["git", "-C", str(root), "-c", "core.hooksPath=/dev/null"]


def _git(root: Path, *args: str, raw: bool = False, reason: int = REASON_GIT_VERIFICATION_FAILED):
    try:
        out = subprocess.check_output(
            _gb(root) + list(args),
            stdin=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=not raw,
            timeout=60,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise ReconcileError(reason, "git verification failed") from exc
    return out if raw else out.strip()


def _git_quiet(root: Path, *args: str) -> int:
    try:
        result = subprocess.run(
            _gb(root) + list(args),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise ReconcileError(REASON_GIT_VERIFICATION_FAILED, "git verification failed") from exc
    if result.returncode not in {0, 1}:
        raise ReconcileError(REASON_GIT_VERIFICATION_FAILED, "git verification failed")
    return result.returncode


def _is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    return _git_quiet(root, "merge-base", "--is-ancestor", ancestor, descendant) == 0


def _safe_path(root: Path, rel: str) -> Path:
    part = PurePosixPath(rel)
    if part.is_absolute() or not part.parts or any(p in {"", ".", ".."} for p in part.parts):
        raise ReconcileError(REASON_UNSUPPORTED_PATH, "unsupported tracked path")
    if root.is_symlink() or not root.is_dir():
        raise ReconcileError(REASON_INVALID_ROOT, "invalid root")
    current = root
    for component in part.parts:
        current = current / component
        if current.exists() and current.is_symlink():
            raise ReconcileError(REASON_UNSUPPORTED_PATH, "unsupported tracked path")
    return current


def _entry(root: Path, commit: str, rel: str):
    raw = _git(root, "ls-tree", "-z", commit, "--", rel, raw=True)
    if not raw:
        return None
    entries = [item for item in raw.split(b"\0") if item]
    if len(entries) != 1:
        raise ReconcileError(REASON_UNSUPPORTED_PATH, "unsupported tracked entry")
    meta, raw_path = entries[0].split(b"\t", 1)
    mode, kind, obj = meta.decode("ascii").split()
    if raw_path.decode("utf-8", "strict") != rel or kind != "blob" or mode not in {"100644", "100755"}:
        raise ReconcileError(REASON_UNSUPPORTED_PATH, "unsupported tracked entry")
    return {"mode": 0o755 if mode == "100755" else 0o644, "object": obj}


def _expected(root: Path, entry):
    if entry is None:
        return None
    data = _git(root, "cat-file", "blob", entry["object"], raw=True)
    if len(data) > MAX_FILE_BYTES:
        raise ReconcileError(REASON_UNSUPPORTED_PATH, "tracked file too large")
    return {"sha256": hashlib.sha256(data).hexdigest(), "mode": entry["mode"], "data": data}


def _live(path: Path):
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise ReconcileError(REASON_UNSUPPORTED_PATH, "unsupported live tracked entry")
    data = path.read_bytes()
    if len(data) > MAX_FILE_BYTES:
        raise ReconcileError(REASON_UNSUPPORTED_PATH, "tracked file too large")
    return {"sha256": hashlib.sha256(data).hexdigest(), "mode": stat.S_IMODE(path.stat().st_mode), "data": data}


def _same(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return a["sha256"] == b["sha256"] and a["mode"] == b["mode"]


def _atomic_set(path: Path, target) -> None:
    if target is None:
        if path.exists():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".vmops-parent-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(target["data"])
            handle.flush()
            os.fchmod(handle.fileno(), target["mode"])
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _assert_no_staged(root: Path) -> None:
    if _git_quiet(root, "diff", "--cached", "--quiet", "--ignore-submodules=all", "--") != 0:
        raise ReconcileError(REASON_STAGED_DRIFT, "staged tracked drift")


def _dirty_paths(root: Path):
    raw = _git(
        root,
        "diff",
        "--name-only",
        "-z",
        "--no-renames",
        "--ignore-submodules=all",
        "--",
        raw=True,
    )
    paths = [item.decode("utf-8", "strict") for item in raw.split(b"\0") if item]
    if len(paths) > MAX_DIRTY_PATHS:
        raise ReconcileError(REASON_SCAN_BOUND, "tracked drift scan exceeded bound")
    return paths


def _matches_reviewed_lineage(root: Path, old: str, new: str, rel: str, live) -> bool:
    commits = _git(root, "rev-list", "--reverse", f"{old}..{new}", "--", rel).split()
    if len(commits) > MAX_LINEAGE_COMMITS:
        raise ReconcileError(REASON_SCAN_BOUND, "reviewed lineage exceeded bound")
    for commit in commits:
        if _same(live, _expected(root, _entry(root, commit, rel))):
            return True
    return False


def reconcile(root: Path, old: str, new: str) -> None:
    if not root.is_absolute() or not root.is_dir() or root.is_symlink():
        raise ReconcileError(REASON_INVALID_ROOT, "invalid root")
    if not SHA_RE.fullmatch(old) or not SHA_RE.fullmatch(new):
        raise ReconcileError(REASON_INVALID_SHA, "invalid SHA")
    if _git(root, "rev-parse", "HEAD", reason=REASON_ROOT_MOVED) != old:
        raise ReconcileError(REASON_ROOT_MOVED, "root moved")
    _git(root, "cat-file", "-e", f"{old}^{{commit}}")
    _git(root, "cat-file", "-e", f"{new}^{{commit}}")
    if not _is_ancestor(root, old, new):
        raise ReconcileError(REASON_NOT_DESCENDANT, "candidate is not a reviewed descendant")

    _assert_no_staged(root)
    dirty = _dirty_paths(root)
    if not dirty:
        return

    plans = []
    for rel in dirty:
        path = _safe_path(root, rel)
        old_expected = _expected(root, _entry(root, old, rel))
        live = _live(path)
        # A path reported dirty relative to old must be a regular tracked entry
        # in old.  New/untracked paths are intentionally outside this helper.
        if old_expected is None:
            raise ReconcileError(REASON_UNSUPPORTED_PATH, "unsupported tracked transition")
        if _same(live, old_expected):
            continue
        if not _matches_reviewed_lineage(root, old, new, rel, live):
            raise ReconcileError(REASON_UNKNOWN_DRIFT, "tracked drift is not reviewed lineage")
        plans.append((path, old_expected, live))

    applied = []
    try:
        # Re-check immutable preconditions immediately before the first write.
        if _git(root, "rev-parse", "HEAD", reason=REASON_ROOT_MOVED) != old:
            raise ReconcileError(REASON_ROOT_MOVED, "root moved")
        _assert_no_staged(root)
        for path, old_expected, original in plans:
            if not _same(_live(path), original):
                raise ReconcileError(REASON_CONCURRENT_DRIFT, "concurrent tracked drift")
            _atomic_set(path, old_expected)
            applied.append((path, old_expected, original))

        _assert_no_staged(root)
        if _dirty_paths(root):
            raise ReconcileError(REASON_POSTVERIFY_FAILED, "parent worktree did not converge")
        if _git(root, "rev-parse", "HEAD", reason=REASON_POSTVERIFY_FAILED) != old:
            raise ReconcileError(REASON_POSTVERIFY_FAILED, "parent worktree moved")
    except Exception as exc:
        rollback_failed = False
        for path, old_expected, original in reversed(applied):
            try:
                if _same(_live(path), old_expected):
                    _atomic_set(path, original)
                elif not _same(_live(path), original):
                    rollback_failed = True
            except Exception:
                rollback_failed = True
        if rollback_failed:
            raise ReconcileError(REASON_MUTATION_FAILED, "rollback incomplete") from exc
        if isinstance(exc, ReconcileError):
            raise
        raise ReconcileError(REASON_MUTATION_FAILED, "parent reconciliation failed") from exc


def main(argv) -> int:
    if len(argv) != 4:
        raise ReconcileError(REASON_INVALID_SHA, "invalid reconcile arguments")
    reconcile(Path(argv[1]), argv[2], argv[3])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except ReconcileError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        raise SystemExit(exc.code)
