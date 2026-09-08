#!/usr/bin/env python3
"""Restore a reviewed pre-synced projection to the recorded old gitlink.

This is a bounded recovery primitive used only after the docich root and owned
Soren checkout have already been proven to be at the recorded old deployment.
For every path changed by old_sub..new_sub, the live projection must be exactly
the old or reviewed-new bytes *and* mode. Reviewed-new paths are atomically
restored to old so the normal root-owned gateway can perform the canonical
old->new deployment. Any third state, symlink, unsupported git entry, or
concurrent drift is refused.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
ALLOWED_PROJECTIONS = {"games/soviet_now": Path("/home/ubuntu/soren")}

REASON_INVALID_INPUT = 70
REASON_ROOT_MISMATCH = 71
REASON_SUBMODULE_MISMATCH = 72
REASON_UNSUPPORTED_PATH = 73
REASON_UNKNOWN_LIVE_STATE = 74
REASON_MUTATION_FAILED = 75
REASON_POSTVERIFY_FAILED = 76
REASON_GIT_FAILED = 77


class NormalizeError(RuntimeError):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def _git(root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), "-c", "core.hooksPath=/dev/null", *args],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise NormalizeError(REASON_GIT_FAILED, "git verification failed") from exc


def _is_ancestor(root: Path, old: str, new: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "-c", "core.hooksPath=/dev/null", "merge-base", "--is-ancestor", old, new],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise NormalizeError(REASON_GIT_FAILED, "git ancestry verification failed") from exc
    if result.returncode not in {0, 1}:
        raise NormalizeError(REASON_GIT_FAILED, "git ancestry verification failed")
    return result.returncode == 0


def _changed_paths(repo: Path, old: str, new: str) -> list[str]:
    try:
        raw = subprocess.check_output(
            ["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null", "diff", "--name-only", "-z", "--no-renames", old, new, "--"],
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise NormalizeError(REASON_GIT_FAILED, "git diff failed") from exc
    return [item.decode("utf-8", "strict") for item in raw.split(b"\0") if item]


def _entry(repo: Path, commit: str, rel: str):
    try:
        raw = subprocess.check_output(
            ["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null", "ls-tree", "-z", commit, "--", rel],
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise NormalizeError(REASON_GIT_FAILED, "git tree lookup failed") from exc
    if not raw:
        return None
    entries = [item for item in raw.split(b"\0") if item]
    if len(entries) != 1:
        raise NormalizeError(REASON_UNSUPPORTED_PATH, "ambiguous tree entry")
    meta, raw_path = entries[0].split(b"\t", 1)
    mode, kind, obj = meta.decode("ascii").split()
    if raw_path.decode("utf-8", "strict") != rel or kind != "blob" or mode not in {"100644", "100755"}:
        raise NormalizeError(REASON_UNSUPPORTED_PATH, "projection path is not a regular tracked file")
    return {"mode": 0o755 if mode == "100755" else 0o644, "object": obj}


def _blob(repo: Path, obj: str) -> bytes:
    try:
        data = subprocess.check_output(
            ["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null", "cat-file", "blob", obj],
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise NormalizeError(REASON_GIT_FAILED, "git blob lookup failed") from exc
    if len(data) > 32 * 1024 * 1024:
        raise NormalizeError(REASON_UNSUPPORTED_PATH, "projection file too large")
    return data


def _expected(repo: Path, entry):
    if entry is None:
        return None
    data = _blob(repo, entry["object"])
    return {"sha256": hashlib.sha256(data).hexdigest(), "mode": entry["mode"], "data": data}


def _safe_path(destination: Path, rel: str) -> Path:
    part = PurePosixPath(rel)
    if part.is_absolute() or not part.parts or any(p in {"", ".", ".."} for p in part.parts):
        raise NormalizeError(REASON_UNSUPPORTED_PATH, "unsafe projection path")
    if destination.is_symlink() or not destination.is_dir():
        raise NormalizeError(REASON_UNSUPPORTED_PATH, "unsafe projection root")
    current = destination
    for component in part.parts:
        current = current / component
        if current.exists() and current.is_symlink():
            raise NormalizeError(REASON_UNSUPPORTED_PATH, "symlink in projection path")
    return current


def _live(path: Path):
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise NormalizeError(REASON_UNSUPPORTED_PATH, "projection path is not a regular file")
    data = path.read_bytes()
    return {"sha256": hashlib.sha256(data).hexdigest(), "mode": stat.S_IMODE(path.stat().st_mode), "data": data}


def _same(meta, expected) -> bool:
    if meta is None or expected is None:
        return meta is None and expected is None
    return meta["sha256"] == expected["sha256"] and meta["mode"] == expected["mode"]


def _atomic_write(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".vmops-normalize-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fchmod(handle.fileno(), mode)
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


def _apply(path: Path, target) -> None:
    if target is None:
        if path.exists():
            path.unlink()
    else:
        _atomic_write(path, target["data"], target["mode"])


def normalize(root: Path, old_parent: str, old_sub: str, new_sub: str, sub_path: str, destination: Path) -> None:
    if not root.is_absolute() or not root.is_dir() or not destination.is_absolute():
        raise NormalizeError(REASON_INVALID_INPUT, "invalid root/destination")
    if sub_path not in ALLOWED_PROJECTIONS or destination != ALLOWED_PROJECTIONS[sub_path]:
        raise NormalizeError(REASON_INVALID_INPUT, "projection is not eligible")
    if not all(SHA_RE.fullmatch(value) for value in (old_parent, old_sub, new_sub)) or old_sub == new_sub:
        raise NormalizeError(REASON_INVALID_INPUT, "invalid SHA contract")
    if _git(root, "rev-parse", "HEAD") != old_parent:
        raise NormalizeError(REASON_ROOT_MISMATCH, "production root moved")
    if _git(root, "status", "--porcelain", "--untracked-files=no", "--ignore-submodules=all"):
        raise NormalizeError(REASON_ROOT_MISMATCH, "tracked root drift")

    sub = root / sub_path
    if not sub.is_dir() or _git(sub, "rev-parse", "HEAD") != old_sub:
        raise NormalizeError(REASON_SUBMODULE_MISMATCH, "owned submodule is not normalized")
    if _git(sub, "status", "--porcelain", "--untracked-files=no"):
        raise NormalizeError(REASON_SUBMODULE_MISMATCH, "owned submodule drift")
    _git(sub, "cat-file", "-e", f"{old_sub}^{{commit}}")
    _git(sub, "cat-file", "-e", f"{new_sub}^{{commit}}")
    if not _is_ancestor(sub, old_sub, new_sub):
        raise NormalizeError(REASON_SUBMODULE_MISMATCH, "reviewed target is not a descendant")

    plans = []
    for rel in _changed_paths(sub, old_sub, new_sub):
        path = _safe_path(destination, rel)
        old_meta = _expected(sub, _entry(sub, old_sub, rel))
        new_meta = _expected(sub, _entry(sub, new_sub, rel))
        live_meta = _live(path)
        if _same(live_meta, old_meta):
            continue
        if not _same(live_meta, new_meta):
            raise NormalizeError(REASON_UNKNOWN_LIVE_STATE, "live projection is neither recorded old nor reviewed new")
        plans.append((path, old_meta, new_meta))

    applied = []
    try:
        for path, old_meta, new_meta in plans:
            if not _same(_live(path), new_meta):
                raise NormalizeError(REASON_UNKNOWN_LIVE_STATE, "concurrent projection drift")
            _apply(path, old_meta)
            applied.append((path, old_meta, new_meta))
        for rel in _changed_paths(sub, old_sub, new_sub):
            path = _safe_path(destination, rel)
            if not _same(_live(path), _expected(sub, _entry(sub, old_sub, rel))):
                raise NormalizeError(REASON_POSTVERIFY_FAILED, "old projection verification failed")
    except Exception as exc:
        rollback_failed = False
        for path, old_meta, new_meta in reversed(applied):
            try:
                if _same(_live(path), old_meta):
                    _apply(path, new_meta)
                elif not _same(_live(path), new_meta):
                    rollback_failed = True
            except Exception:
                rollback_failed = True
        if rollback_failed:
            raise NormalizeError(REASON_MUTATION_FAILED, "projection rollback incomplete") from exc
        if isinstance(exc, NormalizeError):
            raise
        raise NormalizeError(REASON_MUTATION_FAILED, "projection normalization failed") from exc


def main(argv: list[str]) -> int:
    if len(argv) != 7:
        raise NormalizeError(REASON_INVALID_INPUT, "usage: normalize ROOT OLD_PARENT OLD_SUB NEW_SUB SUB_PATH DESTINATION")
    normalize(Path(argv[1]), argv[2], argv[3], argv[4], argv[5], Path(argv[6]))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except NormalizeError as exc:
        print(f"presynced projection normalize refused: {exc}", file=sys.stderr)
        raise SystemExit(exc.code)
