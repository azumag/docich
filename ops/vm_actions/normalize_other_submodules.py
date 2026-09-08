#!/usr/bin/env python3
"""Bounded normalize of owned submodule checkouts other than games/soviet_now.

Only a clean checkout away from its recorded gitlink is moved to that exact
reviewed object. Tracked drift stays fail-closed with a numeric exit code.
Afterwards, read-only managed candidates (Soren live paths projected by an
earlier deploy but outside the current diff) are compared against blobs at
the recorded old gitlink; any drift reports a 10x code without writing.
The whole file is piped via production exec stdin (gateway cap 16384 bytes).
"""
from __future__ import annotations

import hashlib
import re
import stat
import subprocess
import sys
from pathlib import Path

SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
OTHER_SUBMODULES = ("games/hanjuku-sfc-speedrun",)
SOVIET_SUBMODULE = "games/soviet_now"
SOVIET_LIVE = Path("/home/ubuntu/soren")
# Earlier-deploy Soren live paths outside the current diff. Compared against
# blobs at the recorded old gitlink; derivation is in the repair history.
MANAGED_CANDIDATES = (
    "deploy/title-day/soren-title-day.service",
    "deploy/title-day/soren-title-day.timer",
)

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
REASON_CANDIDATE_BASE = 100


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


def _gitlink_at(root: Path, commit: str, path: str, what: str = "other") -> str:
    line = _git(root, "ls-tree", commit, "--", path)
    fields = line.split()
    if len(fields) < 3 or fields[0] != "160000" or fields[1] != "commit" or not SHA_RE.fullmatch(fields[2]):
        raise NormalizeError(REASON_OLD_GITLINK_MISMATCH, f"expected {what} gitlink is missing")
    return fields[2]


def _git_bytes(root: Path, *args: str) -> bytes:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), "-c", "core.hooksPath=/dev/null", *args],
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise NormalizeError(REASON_GIT_VERIFICATION_FAILED, "git verification failed") from exc


def _candidate_state(soviet: Path, old_sub: str, destination: Path, rel: str) -> int:
    # 0 = full old match, 1 = old content with drifted mode, 2 = otherwise.
    # Reports ordinals only; live bytes, hashes, modes, and paths stay local.
    raw = _git_bytes(soviet, "ls-tree", "-z", old_sub, "--", rel)
    entries = [item for item in raw.split(b"\0") if item]
    expected = None
    if len(entries) == 1:
        meta, raw_path = entries[0].split(b"\t", 1)
        mode, kind, obj = meta.decode("ascii").split()
        if raw_path.decode("utf-8", "strict") == rel and kind == "blob" and mode in {"100644", "100755"}:
            data = _git_bytes(soviet, "cat-file", "blob", obj)
            expected = (hashlib.sha256(data).hexdigest(), 0o755 if mode == "100755" else 0o644)
    path = destination / rel
    if path.is_symlink() or not path.is_file():
        live = None
    else:
        data = path.read_bytes()
        live = (hashlib.sha256(data).hexdigest(), stat.S_IMODE(path.stat().st_mode))
    if live is None or expected is None:
        return 0 if live is None and expected is None else 2
    if live[0] != expected[0]:
        return 2
    return 0 if live[1] == expected[1] else 1


def normalize(root: Path, old_parent: str, soviet_live: Path = SOVIET_LIVE) -> None:
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
    old_sub = _gitlink_at(root, old_parent, SOVIET_SUBMODULE, "soviet")
    soviet = root / SOVIET_SUBMODULE
    states = [_candidate_state(soviet, old_sub, soviet_live, rel) for rel in MANAGED_CANDIDATES]
    if any(states):
        raise NormalizeError(
            REASON_CANDIDATE_BASE + states[0] + 3 * states[1],
            "managed candidate drift outside current diff",
        )


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
