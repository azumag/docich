#!/usr/bin/env python3
"""Bounded post-fetch git maintenance for the production checkout (docich#3344).

Each deploy/rebaseline fetch unbundles a full-history bundle into a new
non-thin pack (tens of MB per deploy) and, before this helper, nothing ever
removed them.  This is the bounded gc path and it is deliberately narrow:

- ``git gc`` runs at most once per ``min_interval_seconds`` (daily in
  production) no matter how often the vm-operations gateway invokes the
  helper, so a burst of operations cannot serialize repeated full repacks
  into the deploy path;
- the prune grace is a knob: every caller uses ``--prune=2.weeks.ago`` so
  recently-written objects can never be collected while a concurrent reader
  might still reference them; only the post-successful-deploy call site may
  pass ``post_deploy`` for ``--prune=now``, because immediately after a
  verified ``reset --hard`` every required object is reachable from HEAD and
  the gateway lock already serializes readers;
- each real submodule gitdir under .git/modules is maintained separately,
  with its own daily state and conservative two-week prune grace;
- on the first run it records a canary (``count-objects -v`` output and a
  bounded ``git fsck`` result) into the state file so the operator can
  compare the object store before and after gc was introduced;
- it always reports ``size-pack`` and sets ``warn_size_exceeded`` past the
  configured threshold (3 GiB in production) so growth stays visible even on
  runs where gc itself is skipped.

The verdict is a single JSON object on stdout which the gateway merges into
the operation result; every failure mode still returns a verdict because
maintenance must never wedge a deploy.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PRUNE_MODES = {"grace": "--prune=2.weeks.ago", "post_deploy": "--prune=now"}
GIT_TIMEOUT = 600
FSCK_TIMEOUT = 300
CANARY_MAX_CHARS = 64 * 1024
STATE_MAX_BYTES = 4096


def _git(root: Path, *args: str, timeout: int = GIT_TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), "-c", "core.hooksPath=/dev/null", *args],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, timeout=timeout)


def _count_objects(root: Path) -> str:
    return _git(root, "count-objects", "-v").stdout


def _size_pack_kib(count_output: str) -> int | None:
    for line in count_output.splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "size-pack":
            try:
                return int(value.strip())
            except ValueError:
                return None
    return None


def _read_state(state_file: Path) -> dict:
    try:
        if state_file.stat().st_size > STATE_MAX_BYTES:
            return {}
        state = json.loads(state_file.read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        # A missing or corrupt marker is treated as a fresh state: the worst
        # outcome is an extra gc or a repeated canary, both safe directions.
        return {}


def _write_state(state_file: Path, state: dict) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".git-gc-", dir=state_file.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, sort_keys=True)
        os.replace(tmp, state_file)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _canary(root: Path) -> dict:
    """One-time baseline record of the object store before gc ever ran.

    ``count-objects -v`` is cheap and recorded verbatim; ``git fsck`` is
    bounded by FSCK_TIMEOUT and only its status plus a truncated log is kept,
    because a full fsck log can be arbitrarily large.
    """
    count_output = _count_objects(root)
    try:
        proc = _git(root, "fsck", "--no-dangling", timeout=FSCK_TIMEOUT)
        fsck = {"status": "ok" if proc.returncode == 0 else "error",
                "exit_code": proc.returncode,
                "output": (proc.stdout + proc.stderr)[:CANARY_MAX_CHARS]}
    except subprocess.TimeoutExpired:
        fsck = {"status": "timeout"}
    except (FileNotFoundError, OSError):
        fsck = {"status": "unavailable"}
    return {"epoch": int(time.time()), "count_objects": count_output[:CANARY_MAX_CHARS],
            "fsck": fsck}


def maintain(root: Path, state_file: Path, min_interval_seconds: int,
             warn_size_kib: int, prune_mode: str = "grace",
             *, now: float | None = None) -> dict:
    if prune_mode not in PRUNE_MODES:
        return {"gc": "error", "reason": "bad_prune_mode"}
    now = time.time() if now is None else now
    state = _read_state(state_file)
    canary = state.get("canary")
    if canary is None:
        canary = _canary(root)
        state["canary"] = canary
        _write_state(state_file, state)
    size_before = _size_pack_kib(_count_objects(root))
    verdict = {"gc": "skipped_interval", "size_pack_kib": size_before,
               "warn_size_exceeded": size_before is not None and size_before > warn_size_kib,
               "last_gc_epoch": state.get("last_gc_epoch"), "canary": canary}
    last = state.get("last_gc_epoch")
    if isinstance(last, (int, float)) and now - last < min_interval_seconds:
        return verdict
    proc = _git(root, "gc", PRUNE_MODES[prune_mode], "--quiet")
    if proc.returncode != 0:
        verdict.update(gc="error", reason="gc_failed")
        return verdict
    size_after = _size_pack_kib(_count_objects(root))
    state["last_gc_epoch"] = int(now)
    _write_state(state_file, state)
    verdict.update(gc="run", prune=PRUNE_MODES[prune_mode],
                   size_pack_kib=size_after, size_pack_kib_before=size_before,
                   warn_size_exceeded=size_after is not None and size_after > warn_size_kib,
                   last_gc_epoch=int(now))
    return verdict



def _module_gitdirs(root: Path) -> list[tuple[str, Path]]:
    """Discover real submodule stores without following symlinks or worktrees."""
    gitdir = root / ".git"
    if gitdir.is_symlink() or not gitdir.is_dir():
        return []
    modules = gitdir / "modules"
    if modules.is_symlink() or not modules.is_dir():
        return []
    found = []
    for parent, dirs, files in os.walk(modules, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(parent) / d).is_symlink()]
        current = Path(parent)
        if "config" in files and (current / "objects").is_dir() and not (current / "objects").is_symlink():
            relative = current.relative_to(modules).as_posix()
            found.append((relative, current))
            # A nested submodule can have its own modules/ directory.
            dirs[:] = [d for d in dirs if d == "modules"]
    return found


def maintain_all(root: Path, state_file: Path, interval: int,
                 warn_size_kib: int, prune_mode: str) -> dict:
    """Parent and modules use independent GC state and separate invocations."""
    parent = maintain(root, state_file, interval, warn_size_kib, prune_mode)
    # Modules are never pruned immediately, even on post-deploy.
    modules = {}
    for name, gitdir in _module_gitdirs(root):
        key = hashlib.sha256(name.encode("utf-8")).hexdigest()[:24]
        module_state = state_file.parent / (state_file.stem + "-modules") / (key + ".json")
        try:
            modules[name] = maintain(gitdir, module_state, interval, warn_size_kib, "grace")
        except (OSError, subprocess.SubprocessError, ValueError):
            modules[name] = {"gc": "error", "reason": "maintenance_failed"}
    # Avoid embedding large fsck canaries from every module in the gateway log.
    parent["modules"] = {
        name: {key: value for key, value in result.items() if key != "canary"}
        for name, result in modules.items()
    }
    return parent


def main() -> int:
    if len(sys.argv) != 6:
        print(json.dumps({"gc": "error", "reason": "usage"}))
        return 2
    root, state_file = Path(sys.argv[1]), Path(sys.argv[2])
    try:
        verdict = maintain_all(root, state_file, int(sys.argv[3]),
                           int(sys.argv[4]), sys.argv[5])
    except Exception:
        verdict = {"gc": "error", "reason": "maintenance_failed"}
    print(json.dumps(verdict, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
