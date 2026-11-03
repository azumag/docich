#!/usr/bin/env python3
"""Plan or apply bounded removal of stale *-browser-profile dirs in completed generations (docich#3122).

Dry-run by default. A generation qualifies only if it passes the shared gates:
RUN_ID shape, canonical state inactive, keep-recent/age, and a completion proof.
The proof is either the hanjuku run record (``terminal_identity``, the stricter
form) or the common ``retired`` marker that game-switch teardown writes for
every corner (``src/docich/runtime_retirement.py``). Only directories named
``browser-profile`` or ``*-browser-profile`` inside the generation directory
are removed; the generation directory itself, hanjuku logs and frames are never
removed. The newest keep_recent generations are skipped regardless, and a
profile tree whose newest entry is younger than the age gate is skipped.
``--allow-unmarked-backlog`` only widens *dry-run* enumeration for generations
retired before the marker existed; it never permits apply. Apply requires the
digest of the reviewed dry-run plan, so the deleted set is exactly the reviewed
set. Errors are fixed codes; no paths or source data are printed.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hanjuku_evidence as evidence

SCHEMA = 1
PROFILE_SUFFIX = "-browser-profile"
# Must match src/docich/runtime_retirement.py (parity test in this directory).
RETIRE_MARKER = "retired"
RETIRE_MARKER_SCHEMA = 1
MAX_MARKER_BYTES = 64 * 1024
DEFAULT_BATCH = 20
MAX_BATCH = 100
MAX_RUNTIME_ENTRIES = 4096
MAX_PROFILE_ENTRIES = 65536
DEFAULT_TARGET_FREE_GIB = 5.0
DEFAULT_KEEP_RECENT = 3
DEFAULT_MIN_AGE_HOURS = 24


def _stamp(st):
    # ctime makes content/metadata replacement detectable even when a writer
    # restores the original size and mtime before apply.
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]


def _is_profile_name(name):
    return name == "browser-profile" or name.endswith(PROFILE_SUFFIX)


def _walk_profile(profile_fd, prefix):
    """Collect (relpath, st, is_dir) for every descendant of the profile dir.

    Any symlink, hardlinked or non-regular entry rejects the whole generation:
    Chromium profile trees must be plain files/dirs and anything else means the
    layout was tampered with. Bounded by MAX_PROFILE_ENTRIES.
    """
    entries = []
    stack = [(profile_fd, prefix)]
    while stack:
        dir_fd, rel = stack.pop()
        try:
            children = os.scandir(dir_fd)
        except OSError:
            raise evidence.EvidenceError("profile_scan")
        try:
            with children:
                for child in children:
                    if len(entries) >= MAX_PROFILE_ENTRIES:
                        raise evidence.EvidenceError("profile_scan_limit")
                    try:
                        st = child.stat(follow_symlinks=False)
                    except OSError:
                        raise evidence.EvidenceError("profile_scan") from None
                    child_rel = f"{rel}/{child.name}" if rel else child.name
                    if stat.S_ISLNK(st.st_mode):
                        raise evidence.EvidenceError("unsafe_file")
                    if stat.S_ISDIR(st.st_mode):
                        entries.append((child_rel, st, True))
                        child_fd = os.open(child.name, evidence.FLAGS | os.O_DIRECTORY, dir_fd=dir_fd)
                        stack.append((child_fd, child_rel))
                        continue
                    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                        raise evidence.EvidenceError("unsafe_file")
                    entries.append((child_rel, st, False))
        finally:
            if dir_fd != profile_fd:
                os.close(dir_fd)
    return entries


def _retire_marker_matches(runtime_fd, name, generation):
    """Whether the common teardown marker proves this generation retired.

    Mirrors the contract written by ``src/docich/runtime_retirement.py``:
    a regular, unsymlinked JSON file naming the same runtime/generation. Any
    read or shape failure is simply "no proof", never an exception.
    """
    try:
        raw = evidence._read(runtime_fd, RETIRE_MARKER, MAX_MARKER_BYTES)
        marker = json.loads(raw)
    except (evidence.EvidenceError, ValueError):
        return False
    return (isinstance(marker, dict)
            and type(marker.get("schema")) is int
            and marker["schema"] == RETIRE_MARKER_SCHEMA
            and marker.get("runtime_id") == name
            and type(marker.get("generation")) is int
            and marker["generation"] == generation)


def _completion_proof(runtime_fd, name, generation):
    """Return "run-record", "retire-marker", or None.

    The hanjuku run record is checked first because it carries terminal
    evidence; the common marker only records a confirmed teardown. A present
    but unsafe run record (symlink/hardlink/oversize) stays fail-closed and
    never falls back to the weaker marker.
    """
    try:
        raw = evidence._read(runtime_fd, "hanjuku_run.json", evidence.MAX_JSON)
    except evidence.EvidenceError as exc:
        if str(exc) != "required_file_missing":
            raise
    else:
        try:
            evidence.terminal_identity(evidence._json(raw), name)
            return "run-record"
        except evidence.EvidenceError as exc:
            if str(exc) in ("unsafe_file", "file_too_large"):
                raise
    if _retire_marker_matches(runtime_fd, name, generation):
        return "retire-marker"
    return None


def _collect(runtimes_fd, canonical, keep_recent, min_age_ns, allow_unmarked):
    """Select reclaim candidates and count why other generations were skipped.

    Per-generation failures stay fail-closed (skip); only bounded-scan
    violations abort the whole plan, as before.
    """
    names = []
    with os.scandir(runtimes_fd) as entries:
        for index, item in enumerate(entries):
            if index >= MAX_RUNTIME_ENTRIES:
                raise evidence.EvidenceError("runtime_scan_limit")
            names.append(item.name)
    ids = sorted(
        int(evidence.RUN_ID.fullmatch(name)[1]) for name in names if evidence.RUN_ID.fullmatch(name)
    )
    protected = set(ids[-keep_recent:]) if keep_recent else set()
    generations = []
    skipped = {}
    kinds = {"run-record": 0, "retire-marker": 0, "unmarked-backlog": 0}

    def _skip(reason):
        skipped[reason] = skipped.get(reason, 0) + 1

    for name in names:
        match = evidence.RUN_ID.fullmatch(name)
        if not match:
            _skip("invalid_runtime_name")
            continue
        generation = int(match[1])
        if generation in protected:
            _skip("keep_recent")
            continue
        fresh = 0
        try:
            evidence._inactive(canonical, name)
        except evidence.EvidenceError as exc:
            if str(exc) == "runtime_active":
                _skip("active_or_retiring")
                continue
            raise
        try:
            with evidence._child(runtimes_fd, name) as runtime_fd:
                proof = _completion_proof(runtime_fd, name, generation)
                if proof is None:
                    if not allow_unmarked:
                        _skip("no_terminal_proof")
                        continue
                    kind = "unmarked-backlog"
                else:
                    kind = proof
                profiles = []
                with os.scandir(runtime_fd) as entries:
                    for item in entries:
                        if not _is_profile_name(item.name):
                            continue
                        st = item.stat(follow_symlinks=False)
                        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                            raise evidence.EvidenceError("unsafe_file")
                        with evidence._child(runtime_fd, item.name) as profile_fd:
                            tree = _walk_profile(profile_fd, "")
                        if (min_age_ns and tree
                                and max(e[1].st_mtime_ns for e in tree) >= min_age_ns):
                            fresh += 1
                            continue
                        profiles.append((item.name, st, tree))
        except evidence.EvidenceError as exc:
            _skip("unsafe_layout" if str(exc) in ("unsafe_file", "profile_scan_limit")
                  else "scan_error")
            continue
        except OSError:
            _skip("scan_error")
            continue
        if fresh:
            _skip("profile_too_recent")
        if not profiles:
            _skip("no_profile")
            continue
        kinds[kind] += 1
        generations.append((generation, name, profiles))
    generations.sort()
    return generations, skipped, kinds


def _free_bytes(root):
    st = os.statvfs(root)
    return st.f_bavail * st.f_frsize


def _digest(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _remove_profile(profile_fd, tree, planned_stamps):
    # Every planned entry is re-stamped against the reviewed plan first
    # (fail-closed on any drift), then files are unlinked and directories
    # removed deepest-first, finally the profile top dir itself.
    files = [(rel, st) for rel, st, is_dir in tree if not is_dir]
    dirs = sorted(((rel, st) for rel, st, is_dir in tree if is_dir),
                  key=lambda item: item[0].count("/"), reverse=True)
    removed = 0
    for rel, _st in files + dirs:
        parent_rel, _, leaf = rel.rpartition("/")
        if parent_rel:
            parent_fd = os.open(parent_rel, evidence.FLAGS | os.O_DIRECTORY,
                                dir_fd=profile_fd)
        else:
            parent_fd = profile_fd
        try:
            current = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
            if _stamp(current) != planned_stamps[rel]:
                raise evidence.EvidenceError("source_changed")
        finally:
            if parent_fd != profile_fd:
                os.close(parent_fd)
    for rel, st in files:
        parent_rel, _, leaf = rel.rpartition("/")
        if parent_rel:
            fd = os.open(parent_rel, evidence.FLAGS | os.O_DIRECTORY,
                         dir_fd=profile_fd)
            try:
                os.unlink(leaf, dir_fd=fd)
            finally:
                os.close(fd)
        else:
            os.unlink(leaf, dir_fd=profile_fd)
        removed += st.st_size
    for rel, _st in dirs:
        parent_rel, _, leaf = rel.rpartition("/")
        if parent_rel:
            fd = os.open(parent_rel, evidence.FLAGS | os.O_DIRECTORY,
                         dir_fd=profile_fd)
            try:
                os.rmdir(leaf, dir_fd=fd)
            finally:
                os.close(fd)
        else:
            os.rmdir(leaf, dir_fd=profile_fd)
    return removed


def run(state_dir, *, apply, batch, expect_plan, target_free_bytes,
        keep_recent=DEFAULT_KEEP_RECENT, min_age_hours=DEFAULT_MIN_AGE_HOURS,
        allow_unmarked_backlog=False):
    if type(batch) is not int or not 1 <= batch <= MAX_BATCH:
        raise evidence.EvidenceError("invalid_batch")
    if type(keep_recent) is not int or not 0 <= keep_recent:
        raise evidence.EvidenceError("invalid_keep_recent")
    if apply and allow_unmarked_backlog:
        # Migration-only census switch: apply stays limited to generations
        # with a real completion proof until a separate owner decision.
        raise evidence.EvidenceError("unmarked_backlog_dry_run_only")
    root = Path(state_dir)
    free_before = _free_bytes(root)
    min_age_ns = int(min_age_hours * 3600 * 1e9)
    with evidence._directory(root) as root_fd, evidence._child(root_fd, "locks") as locks_fd:
        lock = os.open("game-switch.lock", evidence.FLAGS, dir_fd=locks_fd)
        try:
            lock_meta = os.fstat(lock)
            if not stat.S_ISREG(lock_meta.st_mode) or lock_meta.st_nlink != 1:
                raise evidence.EvidenceError("unsafe_lock")
            try:
                fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                raise evidence.EvidenceError("switch_busy") from None
            before = evidence._read(root_fd, "game_switch.json", evidence.MAX_JSON)
            canonical = evidence._json(before)
            evidence._inactive(canonical, "")
            with evidence._child(root_fd, "runtimes") as runtimes_fd:
                generations, skipped, kinds = _collect(
                    runtimes_fd, canonical, keep_recent, min_age_ns,
                    allow_unmarked_backlog)
                selected = generations[:batch]
                plan = [
                    {
                        "runtime_id": name,
                        "profiles": [
                            {
                                "name": pname,
                                "stamp": _stamp(st),
                                "entries": [[rel, *_stamp(est), is_dir]
                                            for rel, est, is_dir in tree],
                            }
                            for pname, st, tree in profiles
                        ],
                    }
                    for _, name, profiles in selected
                ]
                digest = _digest(plan)
                removed = 0
                if apply:
                    if expect_plan != digest:
                        raise evidence.EvidenceError("plan_mismatch")
                    for _, name, profiles in selected:
                        with evidence._child(runtimes_fd, name) as runtime_fd:
                            for pname, st, tree in profiles:
                                current = os.stat(pname, dir_fd=runtime_fd,
                                                  follow_symlinks=False)
                                if _stamp(current) != _stamp(st):
                                    raise evidence.EvidenceError("source_changed")
                                planned_stamps = {rel: _stamp(est) for rel, est, _ in tree}
                                with evidence._child(runtime_fd, pname) as profile_fd:
                                    removed += _remove_profile(profile_fd, tree,
                                                               planned_stamps)
                                os.rmdir(pname, dir_fd=runtime_fd)
            if evidence._read(root_fd, "game_switch.json", evidence.MAX_JSON) != before:
                raise evidence.EvidenceError("source_changed")
            named = os.stat("game-switch.lock", dir_fd=locks_fd, follow_symlinks=False)
            if (named.st_dev, named.st_ino) != (lock_meta.st_dev, lock_meta.st_ino):
                raise evidence.EvidenceError("source_changed")
        finally:
            os.close(lock)
    free_after = _free_bytes(root)
    return {
        "schema": SCHEMA,
        "mode": "apply" if apply else "dry-run",
        "batch": batch,
        "keep_recent": keep_recent,
        "min_age_hours": min_age_hours,
        "allow_unmarked_backlog": allow_unmarked_backlog,
        "generations_verified": len(generations),
        "generations_selected": len(selected),
        "selection": kinds,
        "skipped_counts": dict(sorted(skipped.items())),
        "truncated": len(generations) > batch,
        "plan_sha256": digest,
        "plan": [
            {
                "runtime_id": entry["runtime_id"],
                "profiles": [
                    {"name": p["name"], "entries": len(p["entries"]),
                     "bytes": sum(e[3] for e in p["entries"])}
                    for p in entry["profiles"]
                ],
            }
            for entry in plan
        ],
        "bytes_planned": sum(e[3] for entry in plan for p in entry["profiles"] for e in p["entries"]),
        "bytes_removed": removed,
        "free_bytes_before": free_before,
        "free_bytes_after": free_after,
        "target_reached": free_after >= target_free_bytes,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("state_dir")
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expect-plan", default="")
    parser.add_argument("--target-free-gib", type=float, default=DEFAULT_TARGET_FREE_GIB)
    parser.add_argument("--keep-recent", type=int, default=DEFAULT_KEEP_RECENT)
    parser.add_argument("--min-age-hours", type=float, default=DEFAULT_MIN_AGE_HOURS)
    parser.add_argument("--allow-unmarked-backlog", action="store_true",
                        help="dry-run only: also list generations retired before the "
                             "common retire marker existed (never applies)")
    args = parser.parse_args(argv)
    try:
        result = run(args.state_dir, apply=args.apply, batch=args.batch,
                     expect_plan=args.expect_plan,
                     target_free_bytes=int(args.target_free_gib * 1024 ** 3),
                     keep_recent=args.keep_recent, min_age_hours=args.min_age_hours,
                     allow_unmarked_backlog=args.allow_unmarked_backlog)
    except evidence.EvidenceError as exc:
        print(json.dumps({"schema": SCHEMA, "status": "rejected", "reason": str(exc)}))
        return 1
    except OSError:
        print(json.dumps({"schema": SCHEMA, "status": "rejected", "reason": "io_error"}))
        return 1
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
