#!/usr/bin/env python3
"""Bounded reconcile of one reviewed pre-synced Soren state before production deploy."""
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
ALLOWED_SUBMODULES = {"games/soviet_now"}
LIVE_PROJECTION = Path("/home/ubuntu/soren")

# Refusal reasons are numeric only: exec output is withheld, so the exit code
# is the whole signal. Codes stay below the shell signal range (>=128) and
# expose no live bytes, hashes, modes, or private paths.
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
REASON_PROJECTION_UNSUPPORTED = 62
REASON_PROJECTION_UNKNOWN_STATE = 63
REASON_PROJECTION_MUTATION_FAILED = 64
REASON_PROJECTION_POSTVERIFY_FAILED = 65

# Small diffs (2-4 paths): bit N of the mask = path N in git-diff order is
# unknown. Codes 71..85. Anything else unknown-related keeps generic 63.
REASON_PROJECTION_UNKNOWN_MASK_BASE = 70
PROJECTION_UNKNOWN_MASK_MAX_PATHS = 4

# Exactly one unknown path: 90 + bit*3 + class. Class 0 = live absent, 1 =
# live present with unreviewed length, 2 = live present with a reviewed
# length but unreviewed bytes. Codes 90..101. Acceptance never changes.
# Only pruned repo-only paths (never executed on the host) heal to recorded
# old bytes. Edits, runtime paths, and anything else unknown stay fail-closed.
REPO_ONLY_PREFIXES = (".github/",)
REASON_PROJECTION_UNKNOWN_CLASS_BASE = 90
PROJECTION_UNKNOWN_CLASS_PER_PATH = 3
PROJECTION_UNKNOWN_CLASS_ABSENT = 0
PROJECTION_UNKNOWN_CLASS_RESIZED = 1
PROJECTION_UNKNOWN_CLASS_SAME_LENGTH = 2


class ReconcileError(RuntimeError):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def _git_base(root: Path) -> list:
    return ["git", "-C", str(root), "-c", "core.hooksPath=/dev/null"]


def _git(root: Path, *args: str, reason: int = REASON_GIT_VERIFICATION_FAILED, raw: bool = False):
    try:
        out = subprocess.check_output(
            _git_base(root) + list(args),
            stderr=subprocess.DEVNULL,
            text=not raw,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise ReconcileError(reason, "git verification failed") from exc
    return out if raw else out.strip()


def _git_run(root: Path, *args: str) -> None:
    try:
        subprocess.run(
            _git_base(root) + list(args),
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
        result = subprocess.run(
            _git_base(root) + ["merge-base", "--is-ancestor", ancestor, descendant],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise ReconcileError(REASON_GIT_VERIFICATION_FAILED, "git ancestry failed") from exc
    if result.returncode not in {0, 1}:
        raise ReconcileError(REASON_GIT_VERIFICATION_FAILED, "git ancestry failed")
    return result.returncode == 0


def _gitlink_at(root: Path, commit: str, path: str) -> str:
    line = _git(root, "ls-tree", commit, "--", path)
    fields = line.split()
    if len(fields) < 3 or fields[0] != "160000" or fields[1] != "commit" or not SHA_RE.fullmatch(fields[2]):
        raise ReconcileError(REASON_OLD_GITLINK_MISMATCH, "expected gitlink missing")
    return fields[2]


def _changed_paths(repo: Path, old: str, new: str) -> list[str]:
    raw = _git(repo, "diff", "--name-only", "-z", "--no-renames", old, new, "--", raw=True)
    return [item.decode("utf-8", "strict") for item in raw.split(b"\0") if item]


def _projection_entry(repo: Path, commit: str, rel: str):
    raw = _git(repo, "ls-tree", "-z", commit, "--", rel, raw=True)
    if not raw:
        return None
    entries = [item for item in raw.split(b"\0") if item]
    if len(entries) != 1:
        raise ReconcileError(REASON_PROJECTION_UNSUPPORTED, "ambiguous projection entry")
    meta, raw_path = entries[0].split(b"\t", 1)
    mode, kind, obj = meta.decode("ascii").split()
    if raw_path.decode("utf-8", "strict") != rel or kind != "blob" or mode not in {"100644", "100755"}:
        raise ReconcileError(REASON_PROJECTION_UNSUPPORTED, "projection supports regular tracked files only")
    return {"mode": 0o755 if mode == "100755" else 0o644, "object": obj}


def _projection_expected(repo: Path, entry):
    if entry is None:
        return None
    data = _git(repo, "cat-file", "blob", entry["object"], raw=True)
    if len(data) > 32 * 1024 * 1024:
        raise ReconcileError(REASON_PROJECTION_UNSUPPORTED, "projection file too large")
    return {"sha256": hashlib.sha256(data).hexdigest(), "mode": entry["mode"], "data": data}


def _safe_projection_path(destination: Path, rel: str) -> Path:
    part = PurePosixPath(rel)
    if part.is_absolute() or not part.parts or any(p in {"", ".", ".."} for p in part.parts):
        raise ReconcileError(REASON_PROJECTION_UNSUPPORTED, "unsafe projection path")
    if destination.is_symlink() or not destination.is_dir():
        raise ReconcileError(REASON_PROJECTION_UNSUPPORTED, "unsafe projection root")
    current = destination
    for component in part.parts:
        current = current / component
        if current.exists() and current.is_symlink():
            raise ReconcileError(REASON_PROJECTION_UNSUPPORTED, "symlink in projection path")
    return current


def _projection_live(path: Path):
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise ReconcileError(REASON_PROJECTION_UNSUPPORTED, "projection path is not a regular file")
    data = path.read_bytes()
    return {"sha256": hashlib.sha256(data).hexdigest(), "mode": stat.S_IMODE(path.stat().st_mode), "data": data}


def _projection_same(actual, expected) -> bool:
    if actual is None or expected is None:
        return actual is None and expected is None
    return actual["sha256"] == expected["sha256"] and actual["mode"] == expected["mode"]


def _content_sha(meta):
    return meta["sha256"] if meta is not None else None


def _projection_content_state(actual, old_expected, new_expected) -> int:
    live = _content_sha(actual)
    if live == _content_sha(old_expected):
        return 0
    return 1 if live == _content_sha(new_expected) else 2


def _projection_unknown_reason(states: list[int], unknowns=None) -> int:
    # Mask/generic mapping; a lone unknown with its record refines to its
    # presence/length class. Acceptance never changes.
    if len(states) < 2 or len(states) > PROJECTION_UNKNOWN_MASK_MAX_PATHS:
        return REASON_PROJECTION_UNKNOWN_STATE
    mask = sum(1 << index for index, state in enumerate(states) if state == 2)
    if mask == 0 or unknowns is None or mask & (mask - 1) or not unknowns:
        if mask == 0:
            return REASON_PROJECTION_UNKNOWN_STATE
        return REASON_PROJECTION_UNKNOWN_MASK_BASE + mask
    bit, live, old_expected, new_expected = unknowns[0]
    if live is None:
        klass = PROJECTION_UNKNOWN_CLASS_ABSENT
    else:
        known_lens = {len(e["data"]) for e in (old_expected, new_expected) if e is not None}
        klass = PROJECTION_UNKNOWN_CLASS_SAME_LENGTH if len(live["data"]) in known_lens else PROJECTION_UNKNOWN_CLASS_RESIZED
    return REASON_PROJECTION_UNKNOWN_CLASS_BASE + bit * PROJECTION_UNKNOWN_CLASS_PER_PATH + klass


def _atomic_projection_write(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".vmops-reconcile-", dir=path.parent)
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


def _set_projection(path: Path, target) -> None:
    if target is None:
        if path.exists():
            path.unlink()
    else:
        _atomic_projection_write(path, target["data"], target["mode"])


def _normalize_projection(repo: Path, old_sub: str, new_sub: str, destination: Path) -> None:
    plans = []
    verifies = []
    states = []
    unknowns = []
    changed = _changed_paths(repo, old_sub, new_sub)
    for index, rel in enumerate(changed):
        path = _safe_projection_path(destination, rel)
        old_meta = _projection_expected(repo, _projection_entry(repo, old_sub, rel))
        new_meta = _projection_expected(repo, _projection_entry(repo, new_sub, rel))
        live_meta = _projection_live(path)
        verifies.append((path, old_meta))
        state = _projection_content_state(live_meta, old_meta, new_meta)
        if state == 2 and live_meta is None and old_meta is not None and rel.startswith(REPO_ONLY_PREFIXES):
            # Pruned repo-only file: restore recorded old through the same
            # preflighted atomic write + rollback as reviewed writes. Coded
            # as not-refused; verifies/postverify still enforce old content.
            plans.append((path, old_meta, None))
            states.append(0)
            continue
        states.append(state)
        if state == 2:
            unknowns.append((index, live_meta, old_meta, new_meta))
            continue
        if _projection_same(live_meta, old_meta):
            continue
        # Keep the exact live bytes+mode for rollback of reviewed writes.
        plans.append((path, old_meta, live_meta))

    if 2 in states:
        raise ReconcileError(
            _projection_unknown_reason(states, unknowns),
            "live projection content is neither recorded old nor reviewed new",
        )

    applied = []
    try:
        for path, old_meta, original_live in plans:
            if not _projection_same(_projection_live(path), original_live):
                raise ReconcileError(REASON_PROJECTION_UNKNOWN_STATE, "concurrent projection drift")
            _set_projection(path, old_meta)
            applied.append((path, old_meta, original_live))
        for path, old_meta in verifies:
            if not _projection_same(_projection_live(path), old_meta):
                raise ReconcileError(REASON_PROJECTION_POSTVERIFY_FAILED, "old projection postverify failed")
    except Exception as exc:
        rollback_failed = False
        for path, old_meta, original_live in reversed(applied):
            try:
                if _projection_same(_projection_live(path), old_meta):
                    _set_projection(path, original_live)
                elif not _projection_same(_projection_live(path), original_live):
                    rollback_failed = True
            except Exception:
                rollback_failed = True
        if rollback_failed:
            raise ReconcileError(REASON_PROJECTION_MUTATION_FAILED, "projection rollback incomplete") from exc
        if isinstance(exc, ReconcileError):
            raise
        raise ReconcileError(REASON_PROJECTION_MUTATION_FAILED, "projection normalization failed") from exc


def reconcile(
    root: Path,
    old_parent: str,
    old_sub: str,
    new_sub: str,
    sub_path: str,
    projection_destination: Path | None = None,
) -> None:
    if not root.is_absolute() or not root.is_dir():
        raise ReconcileError(REASON_INVALID_ROOT, "invalid production root")
    if sub_path not in ALLOWED_SUBMODULES:
        raise ReconcileError(REASON_UNAPPROVED_SUBMODULE, "submodule not eligible for reconcile")
    if not all(SHA_RE.fullmatch(value) for value in (old_parent, old_sub, new_sub)):
        raise ReconcileError(REASON_INVALID_SHA, "invalid reconcile SHA")
    if old_sub == new_sub:
        raise ReconcileError(REASON_NO_ADVANCE, "submodule did not advance")

    if _git(root, "rev-parse", "HEAD", reason=REASON_ROOT_MOVED) != old_parent:
        raise ReconcileError(REASON_ROOT_MOVED, "production root moved")
    if _git(root, "status", "--porcelain", "--untracked-files=no", "--ignore-submodules=all", reason=REASON_ROOT_DRIFT):
        raise ReconcileError(REASON_ROOT_DRIFT, "tracked production root drift")
    if _gitlink_at(root, old_parent, sub_path) != old_sub:
        raise ReconcileError(REASON_OLD_GITLINK_MISMATCH, "old gitlink mismatch")

    sub = root / sub_path
    if not sub.is_dir():
        raise ReconcileError(REASON_SUBMODULE_MISSING, "submodule checkout missing")

    current_sub = _git(sub, "rev-parse", "HEAD", reason=REASON_SUBMODULE_HEAD_MISMATCH)
    if current_sub not in {old_sub, new_sub}:
        current_tree = _git(sub, "rev-parse", f"{current_sub}^{{tree}}", reason=REASON_SUBMODULE_HEAD_MISMATCH)
        reviewed_tree = _git(sub, "rev-parse", f"{new_sub}^{{tree}}", reason=REASON_NEW_OBJECT_MISSING)
        if current_tree != reviewed_tree or not _is_ancestor(sub, current_sub, new_sub):
            raise ReconcileError(REASON_SUBMODULE_HEAD_MISMATCH, "submodule not at accepted state")

    if _git(sub, "status", "--porcelain", "--untracked-files=no", reason=REASON_SUBMODULE_DRIFT):
        raise ReconcileError(REASON_SUBMODULE_DRIFT, "submodule tracked drift")

    _git(sub, "cat-file", "-e", f"{old_sub}^{{commit}}", reason=REASON_OLD_OBJECT_MISSING)
    _git(sub, "cat-file", "-e", f"{new_sub}^{{commit}}", reason=REASON_NEW_OBJECT_MISSING)
    if not _is_ancestor(sub, old_sub, new_sub):
        raise ReconcileError(REASON_NOT_DESCENDANT, "target not descendant of old")

    if current_sub != old_sub:
        _git_run(sub, "checkout", "--detach", "--quiet", old_sub)

    if _git(sub, "rev-parse", "HEAD", reason=REASON_POSTVERIFY_FAILED) != old_sub:
        raise ReconcileError(REASON_POSTVERIFY_FAILED, "submodule postverify failed")
    if _git(sub, "status", "--porcelain", "--untracked-files=no", reason=REASON_POSTVERIFY_FAILED):
        raise ReconcileError(REASON_POSTVERIFY_FAILED, "submodule postverify failed")

    if projection_destination is not None:
        _normalize_projection(sub, old_sub, new_sub, projection_destination)


def main(argv: list[str]) -> int:
    if len(argv) != 6:
        raise ReconcileError(REASON_INVALID_SHA, "usage: reconcile ROOT OLD_PARENT OLD_SUB NEW_SUB SUB_PATH")
    reconcile(Path(argv[1]), argv[2], argv[3], argv[4], argv[5], LIVE_PROJECTION)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except ReconcileError as exc:
        print(f"presynced reconcile refused: {exc}", file=sys.stderr)
        raise SystemExit(exc.code)
