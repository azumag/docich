#!/usr/bin/env python3
"""Plan or apply bounded removal of non-evidence logs in completed Hanjuku generations (docich#3109).

Dry-run by default. A generation qualifies only if it passes the same gates as
hanjuku_evidence_gateway._completed_candidates: RUN_ID shape, canonical state
inactive, terminal_identity. Only the files in BULKY_NAMES are removed; the
generation directory itself is never removed. Apply requires the digest of the
reviewed dry-run plan, so the deleted set is exactly the reviewed set.
Errors are fixed codes; no paths or source data are printed.
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
# Logs the evidence exporter never reads (hanjuku_evidence.FILES excludes them;
# test_hanjuku_evidence asserts the same). decisions/events/chart_history are
# evidence inputs and are deliberately NOT listed here.
BULKY_NAMES = frozenset({"hanjuku_commentary.jsonl", "hanjuku_narration.jsonl"})
DEFAULT_BATCH = 20
MAX_BATCH = 100
MAX_RUNTIME_ENTRIES = 4096
DEFAULT_TARGET_FREE_GIB = 5.0


def _stamp(st):
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns]


def _bulky_files(runtime_fd):
    files = []
    for name in sorted(BULKY_NAMES):
        try:
            st = os.stat(name, dir_fd=runtime_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise evidence.EvidenceError("unsafe_file")
        files.append((name, st))
    return files


def _collect(runtimes_fd, canonical):
    """Return [(generation, runtime_id, [(file_name, stat)])] for verified completed runs."""
    names = []
    with os.scandir(runtimes_fd) as entries:
        for index, item in enumerate(entries):
            if index >= MAX_RUNTIME_ENTRIES:
                raise evidence.EvidenceError("runtime_scan_limit")
            names.append(item.name)
    generations = []
    for name in names:
        match = evidence.RUN_ID.fullmatch(name)
        if not match:
            continue
        try:
            evidence._inactive(canonical, name)
        except evidence.EvidenceError as exc:
            if str(exc) == "runtime_active":
                continue
            raise
        try:
            with evidence._child(runtimes_fd, name) as runtime_fd:
                run = evidence._json(evidence._read(runtime_fd, "hanjuku_run.json", evidence.MAX_JSON))
                evidence.terminal_identity(run, name)
                files = _bulky_files(runtime_fd)
        except (OSError, evidence.EvidenceError):
            continue  # fail-closed per generation: unverifiable runs are left alone
        if files:
            generations.append((int(match[1]), name, files))
    generations.sort()
    return generations


def _free_bytes(root):
    st = os.statvfs(root)
    return st.f_bavail * st.f_frsize


def _digest(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def run(state_dir, *, apply, batch, expect_plan, target_free_bytes):
    if type(batch) is not int or not 1 <= batch <= MAX_BATCH:
        raise evidence.EvidenceError("invalid_batch")
    root = Path(state_dir)
    free_before = _free_bytes(root)
    with evidence._directory(root) as root_fd, evidence._child(root_fd, "locks") as locks_fd:
        lock = os.open("game-switch.lock", evidence.FLAGS, dir_fd=locks_fd)
        try:
            lock_meta = os.fstat(lock)
            if not stat.S_ISREG(lock_meta.st_mode) or lock_meta.st_nlink != 1:
                raise evidence.EvidenceError("unsafe_lock")
            try:
                # Shared lock: the switch may not change while files are checked and removed.
                fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                raise evidence.EvidenceError("switch_busy") from None
            before = evidence._read(root_fd, "game_switch.json", evidence.MAX_JSON)
            canonical = evidence._json(before)
            evidence._inactive(canonical, "")
            with evidence._child(root_fd, "runtimes") as runtimes_fd:
                generations = _collect(runtimes_fd, canonical)
                selected = generations[:batch]
                plan = [
                    {"runtime_id": name, "files": [[fname, *_stamp(st)] for fname, st in files]}
                    for _, name, files in selected
                ]
                digest = _digest(plan)
                removed = 0
                if apply:
                    if expect_plan != digest:
                        raise evidence.EvidenceError("plan_mismatch")
                    for _, name, files in selected:
                        with evidence._child(runtimes_fd, name) as runtime_fd:
                            for fname, planned in files:
                                # Revalidate the exact inode/size/mtime right before unlinking.
                                current = os.stat(fname, dir_fd=runtime_fd, follow_symlinks=False)
                                if _stamp(current) != _stamp(planned):
                                    raise evidence.EvidenceError("source_changed")
                                os.unlink(fname, dir_fd=runtime_fd)
                                removed += current.st_size
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
        "generations_verified": len(generations),
        "generations_selected": len(selected),
        "truncated": len(generations) > batch,
        "plan_sha256": digest,
        "plan": [
            {
                "runtime_id": entry["runtime_id"],
                "files": [{"name": fname, "bytes": size} for fname, _, _, size, _ in entry["files"]],
            }
            for entry in plan
        ],
        "bytes_planned": sum(size for entry in plan for _, _, _, size, _ in entry["files"]),
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
    args = parser.parse_args(argv)
    try:
        result = run(
            args.state_dir,
            apply=args.apply,
            batch=args.batch,
            expect_plan=args.expect_plan,
            target_free_bytes=int(args.target_free_gib * 1024 ** 3),
        )
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
