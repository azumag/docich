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

Two apply modes exist, both default OFF:

* Manual: ``--apply`` requires ``--expect-plan`` with the sha256 digest of the
  reviewed dry-run plan (unchanged from the first deployment).
* Continuity (ADR 0006 DSH review): ``--auto-apply`` removes only entries that
  were also in the previous pass's plan with byte-for-byte identical stamps
  (dev/ino/size/mtime/ctime), so every automatic removal was reviewed as a
  policy and is unchanged for at least one full pass. It needs a 0600 state
  file outside the opencode store and appends an applied-entry manifest.

Errors are fixed codes; no paths or file contents are printed.
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
import tempfile
import time

SCHEMA = 1
STATE_SCHEMA = 1
MANIFEST_SCHEMA = 1
STATUS_SCHEMA = 1
MAX_TREE_ENTRIES = 200000
MAX_TOP_ENTRIES = 65536
MAX_STATE_BYTES = 64 * 1024 * 1024
DEFAULT_CUTOFF_HOURS = 24.0
DEFAULT_BATCH = 50
MAX_BATCH = 500
DEFAULT_GATE_WAIT_SEC = 120
FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW
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
    return hashlib.sha256(json.dumps(plan_view, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _entry_view(item):
    """Canonical, stamp-complete view of one planned removal.

    The same shape is used for the reviewed plan digest and for the two-pass
    continuity state, so a state entry matches only when every recorded stamp
    is byte-for-byte unchanged.
    """
    return {
        "path": item["path"],
        "kind": item["kind"],
        "top_stamp": item.get("top_stamp"),
        "stamp": item.get("stamp"),
        "entries": [[rel, *(_stamp(st)), is_dir] for rel, st, is_dir in item.get("entries", [])],
    }


def _canonical(view):
    return json.dumps(view, sort_keys=True, separators=(",", ":"))


def _outside_store(path, data_dir):
    """True when path is not inside the scanned store (state/manifest rule)."""
    try:
        real_path = os.path.realpath(path)
        real_data = os.path.realpath(data_dir)
        return os.path.commonpath([real_path, real_data]) != real_data
    except (OSError, ValueError):
        return False


def _load_state(path, data_dir, cutoff_hours):
    """Read the previous dry-run plan, failing closed on any doubt.

    The file must be a regular, non-symlink, single-link mode-0600 file owned
    by the current uid and lie outside the scanned store. Any deviation
    aborts the whole automatic pass instead of applying a partial set.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        raise ReclaimError("state_unreadable") from None
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1
                or st.st_mode & 0o077 or st.st_uid != os.geteuid()):
            raise ReclaimError("state_invalid")
        if st.st_size > MAX_STATE_BYTES:
            raise ReclaimError("state_invalid")
        chunks = []
        total = 0
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_STATE_BYTES:
                raise ReclaimError("state_invalid")
            chunks.append(chunk)
    finally:
        os.close(fd)
    try:
        payload = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ReclaimError("state_invalid") from None
    if not isinstance(payload, dict) or payload.get("schema") != STATE_SCHEMA:
        raise ReclaimError("state_invalid")
    if payload.get("data_dir") != str(data_dir):
        raise ReclaimError("state_invalid")
    if payload.get("cutoff_hours") != float(cutoff_hours):
        raise ReclaimError("state_policy_mismatch")
    entries = payload.get("entries")
    if not isinstance(entries, list) or len(entries) > MAX_TREE_ENTRIES * 2:
        raise ReclaimError("state_invalid")
    for entry in entries:
        if not isinstance(entry, dict):
            raise ReclaimError("state_invalid")
        if not isinstance(entry.get("path"), str) or entry.get("kind") not in {"dir", "file"}:
            raise ReclaimError("state_invalid")
    return entries


def _write_state(path, data_dir, cutoff_hours, views):
    payload = {
        "schema": STATE_SCHEMA,
        "data_dir": str(data_dir),
        "cutoff_hours": float(cutoff_hours),
        "entries": views,
    }
    directory = os.path.dirname(path) or "."
    try:
        fd, tmp = tempfile.mkstemp(prefix=".opencode_session_state.", dir=directory)
    except OSError:
        raise ReclaimError("state_write") from None
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        tmp = ""
    except OSError:
        raise ReclaimError("state_write") from None
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _append_manifest(path, applied):
    """Append one JSON line per automatically removed entry (mode 0600)."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    except OSError:
        raise ReclaimError("manifest_write") from None
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1
                or st.st_mode & 0o077 or st.st_uid != os.geteuid()):
            raise ReclaimError("manifest_invalid")
        for item in applied:
            view = _entry_view(item)
            if item["kind"] == "file":
                size = item["stamp"][2]
                mtime_ns = item["stamp"][3]
            else:
                size = sum(e[1].st_size for e in item["entries"] if not e[2])
                mtime_ns = max((e[1].st_mtime_ns for e in item["entries"]), default=0)
            line = json.dumps(
                {"schema": MANIFEST_SCHEMA, "path": view["path"], "bytes": size,
                 "mtime_ns": mtime_ns, "mode": "auto_apply"},
                sort_keys=True, separators=(",", ":"),
            )
            os.write(fd, (line + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_status(path, data_dir, payload):
    """Best-effort bounded status file for monitor projection.

    The payload is built by ``main`` and contains only fixed enums, booleans
    and non-negative integers (never a plan, path or file content). Any
    failure is swallowed so a status write can never fail the retention unit.
    """
    if not path:
        return False
    try:
        if not _outside_store(path, data_dir):
            return False
        directory = os.path.dirname(path) or "."
        fd, tmp = tempfile.mkstemp(prefix=".opencode_session_status.", dir=directory)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
            tmp = ""
        finally:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
    except (OSError, ValueError, TypeError):
        return False
    return True


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
        dirs = sorted(((r, s) for r, s, d in tree if d), key=lambda e: e[0].count("/"), reverse=True)
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


def run(data_dir, gate_path, *, apply, batch, expect_plan, cutoff_hours, gate_wait_sec,
        auto_apply=False, state_file="", manifest_file=""):
    if type(batch) is not int or not 1 <= batch <= MAX_BATCH:
        raise ReclaimError("invalid_batch")
    if gate_wait_sec < 0:
        raise ReclaimError("invalid_gate_wait")
    if auto_apply and (apply or expect_plan):
        raise ReclaimError("invalid_mode")
    if auto_apply and not state_file:
        raise ReclaimError("state_required")
    if auto_apply and not manifest_file:
        raise ReclaimError("manifest_required")
    if state_file and not _outside_store(state_file, data_dir):
        raise ReclaimError("state_in_store")
    if manifest_file and not _outside_store(manifest_file, data_dir):
        raise ReclaimError("manifest_in_store")
    store = os.path.realpath(data_dir)
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
        confirmed = plan
        if auto_apply:
            # Two-pass continuity: only entries seen in the previous plan with
            # every stamp unchanged may be removed without a human digest.
            previous = _load_state(state_file, store, cutoff_hours)
            index = {entry["path"]: _canonical(entry) for entry in previous}
            confirmed = [i for i in plan if index.get(i["path"]) == _canonical(_entry_view(i))]
        selected = confirmed[:batch]
        plan_view = [_entry_view(i) for i in selected]
        digest = _digest(plan_view)
        removed = 0
        if auto_apply:
            # Record each removal before moving to the next entry so a crash
            # mid-batch cannot leave already removed entries unrecorded.
            for item in selected:
                removed += _apply(data_fd, [item])
                _append_manifest(manifest_file, [item])
            applied = {i["path"] for i in selected}
            remaining = [i for i in plan if i["path"] not in applied]
            _write_state(state_file, store, cutoff_hours, [_entry_view(i) for i in remaining])
        elif apply:
            if expect_plan != digest:
                raise ReclaimError("plan_mismatch")
            removed = _apply(data_fd, selected)
            if state_file:
                applied = {i["path"] for i in selected}
                remaining = [i for i in plan if i["path"] not in applied]
                _write_state(state_file, store, cutoff_hours, [_entry_view(i) for i in remaining])
        elif state_file:
            # Dry-run warms the continuity state; nothing is removed.
            _write_state(state_file, store, cutoff_hours, [_entry_view(i) for i in plan])
        fcntl.flock(gate_fd, fcntl.LOCK_UN)
        return {
            "schema": SCHEMA,
            "mode": "apply" if (apply or auto_apply) else "dry-run",
            "auto_apply": bool(auto_apply),
            "batch": batch,
            "cutoff_hours": cutoff_hours,
            "entries_planned": len(plan),
            "entries_confirmed": len(confirmed),
            "entries_selected": len(selected),
            "truncated": len(confirmed) > batch,
            "plan_sha256": digest,
            "state": "written" if state_file else "disabled",
            "plan": [
                {"path": i["path"], "kind": i["kind"],
                 "bytes": (i["stamp"][2] if i["kind"] == "file"
                           else sum(e[1].st_size for e in i["entries"] if not e[2]))}
                for i in selected
            ],
            "bytes_planned": sum(
                i["stamp"][2] if i["kind"] == "file"
                else sum(e[1].st_size for e in i["entries"] if not e[2])
                for i in selected
            ),
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
    parser.add_argument("--auto-apply", action="store_true")
    parser.add_argument("--state-file", default="")
    parser.add_argument("--manifest-file", default="")
    parser.add_argument("--status-file", default="")
    parser.add_argument("--cutoff-hours", type=float, default=DEFAULT_CUTOFF_HOURS)
    parser.add_argument("--gate-wait-sec", type=float, default=DEFAULT_GATE_WAIT_SEC)
    args = parser.parse_args(argv)
    mode = "apply" if (args.apply or args.auto_apply) else "dry-run"

    def reject(reason):
        _write_status(args.status_file, args.data_dir, {
            "schema": STATUS_SCHEMA,
            "status": "gate_timeout" if reason == "gate_timeout" else "failed",
            "mode": mode,
            "reason": reason,
            "completed_at": int(time.time()),
            "cutoff_seconds": max(0, int(args.cutoff_hours * 3600)),
            "auto_apply": bool(args.auto_apply),
        })

    try:
        result = run(
            args.data_dir, args.gate_path, apply=args.apply, batch=args.batch,
            expect_plan=args.expect_plan, cutoff_hours=args.cutoff_hours,
            gate_wait_sec=args.gate_wait_sec, auto_apply=args.auto_apply,
            state_file=args.state_file, manifest_file=args.manifest_file,
        )
    except ReclaimError as exc:
        reason = str(exc)
        reject(reason)
        print(json.dumps({"schema": SCHEMA, "status": "rejected", "reason": reason}))
        return 75 if reason == "gate_timeout" else 1
    except OSError:
        reject("io_error")
        print(json.dumps({"schema": SCHEMA, "status": "rejected", "reason": "io_error"}))
        return 1
    _write_status(args.status_file, args.data_dir, {
        "schema": STATUS_SCHEMA,
        "status": "completed",
        "mode": result["mode"],
        "reason": "ok",
        "completed_at": int(time.time()),
        "cutoff_seconds": max(0, int(args.cutoff_hours * 3600)),
        "auto_apply": bool(result["auto_apply"]),
        "entries_planned": result["entries_planned"],
        "entries_confirmed": result["entries_confirmed"],
        "entries_selected": result["entries_selected"],
        "bytes_planned": result["bytes_planned"],
        "bytes_removed": result["bytes_removed"],
        "truncated": bool(result["truncated"]),
        "state_written": result["state"] == "written",
    })
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
