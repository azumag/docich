#!/usr/bin/env python3
"""Plan or apply bounded removal of expired opencode session files (docich#3140 / ADR 0006).

Dry-run by default. Targets only the session-file store inside the opencode
default data dir: ``snapshot/<entry>/`` directories and ``storage/session_diff/*.json``
files whose newest content is older than the cutoff (24h, aligned with the
existing DB retention). ``tool-output``, ``log``, ``repos``, ``shell``,
``auth.json``, ``storage/migration`` and ``opencode.db*`` are never touched
(#1906 / ADR 0006 policy). The exclusive producer rotation gate
(``flock -x`` on ``.opencode_rotation_gate.lock``) is held during both plan
and apply, so no ``opencode run`` may write while the mutation window is open.
Apply requires the sha256 digest of the reviewed dry-run plan. Errors are
fixed codes; no paths or file contents are printed.
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
import time

SCHEMA = 1
MAX_TREE_ENTRIES = 200000
MAX_TOP_ENTRIES = 65536
DEFAULT_CUTOFF_HOURS = 24.0
DEFAULT_BATCH = 50
MAX_BATCH = 500
DEFAULT_GATE_WAIT_SEC = 120
FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW
# Fixed exclusion set at the data-dir top level. Anything else is only
# inspected if it matches the fixed target sub-paths below.
TARGET_DIRS = ("snapshot",)
TARGET_FILE_DIRS = (("storage", "session_diff"),)


class ReclaimError(Exception):
    pass


def _stamp(st):
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]


def _open_dir(name, parent_fd):
    return os.open(name, FLAGS | os.O_DIRECTORY, dir_fd=parent_fd)


def _walk(root_fd, prefix):
    """fd-based recursive scan. Any symlink / non-regular entry rejects."""
    entries = []
    stack = [(root_fd, prefix)]
    while stack:
        dir_fd, rel = stack.pop()
        try:
            children = os.scandir(dir_fd)
        except OSError:
            raise ReclaimError("tree_scan")
        try:
            with children:
                for child in children:
                    if len(entries) >= MAX_TREE_ENTRIES:
                        raise ReclaimError("tree_scan_limit")
                    try:
                        st = child.stat(follow_symlinks=False)
                    except OSError:
                        raise ReclaimError("tree_scan") from None
                    child_rel = f"{rel}/{child.name}" if rel else child.name
                    if stat.S_ISLNK(st.st_mode):
                        raise ReclaimError("unsafe_entry")
                    if stat.S_ISDIR(st.st_mode):
                        entries.append((child_rel, st, True))
                        stack.append((_open_dir(child.name, dir_fd), child_rel))
                    elif stat.S_ISREG(st.st_mode) and st.st_nlink == 1:
                        entries.append((child_rel, st, False))
                    else:
                        raise ReclaimError("unsafe_entry")
        finally:
            if dir_fd != root_fd:
                os.close(dir_fd)
    return entries


def _collect(data_fd, cutoff_ns):
    """Return planned removals: snapshot top dirs and session_diff json files."""
    plan = []
    for top in TARGET_DIRS:
        try:
            top_fd = _open_dir(top, data_fd)
        except FileNotFoundError:
            continue
        try:
            with os.scandir(top_fd) as entries:
                for index, item in enumerate(entries):
                    if index >= MAX_TOP_ENTRIES:
                        raise ReclaimError("top_scan_limit")
                    st = item.stat(follow_symlinks=False)
                    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                        raise ReclaimError("unsafe_entry")
                    with os.scandir(top_fd) as _:
                        pass
                    child_fd = _open_dir(item.name, top_fd)
                    try:
                        tree = _walk(child_fd, "")
                    finally:
                        os.close(child_fd)
                    if not tree:
                        continue
                    if max(e[1].st_mtime_ns for e in tree) < cutoff_ns:
                        plan.append({"kind": "dir", "path": f"{top}/{item.name}",
                                     "top_stamp": _stamp(st), "entries": tree})
        finally:
            os.close(top_fd)
    for parent, sub in TARGET_FILE_DIRS:
        try:
            parent_fd = _open_dir(parent, data_fd)
        except FileNotFoundError:
            continue
        try:
            try:
                sub_fd = _open_dir(sub, parent_fd)
            except FileNotFoundError:
                continue
            try:
                with os.scandir(sub_fd) as entries:
                    for index, item in enumerate(entries):
                        if index >= MAX_TOP_ENTRIES:
                            raise ReclaimError("top_scan_limit")
                        st = item.stat(follow_symlinks=False)
                        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                            raise ReclaimError("unsafe_entry")
                        if st.st_mtime_ns < cutoff_ns:
                            plan.append({"kind": "file", "path": f"{parent}/{sub}/{item.name}",
                                         "stamp": _stamp(st)})
            finally:
                os.close(sub_fd)
        finally:
            os.close(parent_fd)
    plan.sort(key=lambda p: p["path"])
    return plan


def _digest(plan_view):
    return hashlib.sha256(
        json.dumps(plan_view, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _verify(data_fd, rel, stamp):
    parent_rel, _, leaf = rel.rpartition("/")
    fd = _open_dir(parent_rel, data_fd) if parent_rel else data_fd
    try:
        current = os.stat(leaf, dir_fd=fd, follow_symlinks=False)
    finally:
        if fd != data_fd:
            os.close(fd)
    if _stamp(current) != stamp:
        raise ReclaimError("source_changed")


def _apply(data_fd, plan):
    removed = 0
    for item in plan:
        if item["kind"] == "file":
            _verify(data_fd, item["path"], item["stamp"])
            parent_rel, _, leaf = item["path"].rpartition("/")
            fd = _open_dir(parent_rel, data_fd)
            try:
                os.unlink(leaf, dir_fd=fd)
            finally:
                os.close(fd)
            removed += item["stamp"][2]
            continue
        # dir tree: verify the top dir and every entry BEFORE removal —
        # deleting children rewrites the parent dir's own stamp, so the top
        # stamp must be checked first (fail-closed on any drift).
        tree = item["entries"]
        _verify(data_fd, item["path"], item["top_stamp"])
        for rel, st, _is_dir in tree:
            _verify(data_fd, f"{item['path']}/{rel}", _stamp(st))
        for rel, st, is_dir in tree:
            full = f"{item['path']}/{rel}"
            parent_rel, _, leaf = full.rpartition("/")
            fd = _open_dir(parent_rel, data_fd)
            try:
                if is_dir:
                    continue
                os.unlink(leaf, dir_fd=fd)
                removed += st.st_size
            finally:
                os.close(fd)
        dirs = sorted(((r, s) for r, s, d in tree if d),
                      key=lambda e: e[0].count("/"), reverse=True)
        for rel, _st in dirs:
            full = f"{item['path']}/{rel}"
            parent_rel, _, leaf = full.rpartition("/")
            fd = _open_dir(parent_rel, data_fd)
            try:
                os.rmdir(leaf, dir_fd=fd)
            finally:
                os.close(fd)
        top_rel, _, leaf = item["path"].rpartition("/")
        fd = _open_dir(top_rel, data_fd)
        try:
            os.rmdir(leaf, dir_fd=fd)
        finally:
            os.close(fd)
    return removed


def run(data_dir, gate_path, *, apply, batch, expect_plan, cutoff_hours,
        gate_wait_sec):
    if type(batch) is not int or not 1 <= batch <= MAX_BATCH:
        raise ReclaimError("invalid_batch")
    if gate_wait_sec < 0:
        raise ReclaimError("invalid_gate_wait")
    root = Path(data_dir)
    cutoff_ns = int((time.time() - cutoff_hours * 3600) * 1e9)
    data_fd = os.open(root, FLAGS | os.O_DIRECTORY)
    gate_fd = os.open(gate_path, os.O_RDONLY | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        deadline = time.monotonic() + gate_wait_sec
        while True:
            try:
                fcntl.flock(gate_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ReclaimError("gate_timeout") from None
                time.sleep(0.2)
        plan = _collect(data_fd, cutoff_ns)
        selected = plan[:batch]
        plan_view = [
            {"path": i["path"], "kind": i["kind"],
             "top_stamp": i.get("top_stamp"), "stamp": i.get("stamp"),
             "entries": [[r, *_stamp(st), d] for r, st, d in i.get("entries", [])]}
            for i in selected
        ]
        digest = _digest(plan_view)
        removed = 0
        if apply:
            if expect_plan != digest:
                raise ReclaimError("plan_mismatch")
            removed = _apply(data_fd, selected)
        fcntl.flock(gate_fd, fcntl.LOCK_UN)
        return {
            "schema": SCHEMA,
            "mode": "apply" if apply else "dry-run",
            "batch": batch,
            "cutoff_hours": cutoff_hours,
            "entries_planned": len(plan),
            "entries_selected": len(selected),
            "truncated": len(plan) > batch,
            "plan_sha256": digest,
            "plan": [{"path": i["path"], "kind": i["kind"],
                      "bytes": (i["stamp"][2] if i["kind"] == "file"
                                else sum(e[1].st_size for e in i["entries"] if not e[2]))}
                     for i in selected],
            "bytes_planned": sum(
                i["stamp"][2] if i["kind"] == "file"
                else sum(e[1].st_size for e in i["entries"] if not e[2])
                for i in selected),
            "bytes_removed": removed,
        }
    finally:
        os.close(gate_fd)
        os.close(data_fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("data_dir")
    parser.add_argument("gate_path")
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expect-plan", default="")
    parser.add_argument("--cutoff-hours", type=float, default=DEFAULT_CUTOFF_HOURS)
    parser.add_argument("--gate-wait-sec", type=float, default=DEFAULT_GATE_WAIT_SEC)
    args = parser.parse_args(argv)
    try:
        result = run(args.data_dir, args.gate_path, apply=args.apply,
                     batch=args.batch, expect_plan=args.expect_plan,
                     cutoff_hours=args.cutoff_hours, gate_wait_sec=args.gate_wait_sec)
    except ReclaimError as exc:
        print(json.dumps({"schema": SCHEMA, "status": "rejected", "reason": str(exc)}))
        return 1
    except OSError:
        print(json.dumps({"schema": SCHEMA, "status": "rejected", "reason": "io_error"}))
        return 1
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
