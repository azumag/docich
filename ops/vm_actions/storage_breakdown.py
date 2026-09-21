#!/usr/bin/env python3
"""Bounded read-only fixed-category storage diagnostics.

This module is intentionally identity-free. It scans only fixed roots supplied
by the trusted production collector, never follows symlinks, never reads file
contents, and returns counts/allocated bytes only. Categories overlap by
design and must not be summed.
"""
import os
import stat
from pathlib import Path

DEFAULT_MAX_ENTRIES = 100000


def _allocated_bytes(st):
    blocks = getattr(st, "st_blocks", None)
    if isinstance(blocks, int) and blocks >= 0:
        return blocks * 512
    return max(0, int(getattr(st, "st_size", 0) or 0))


def _base_result():
    return {
        "present": False,
        "scan_complete": True,
        "count": 0,
        "allocated_bytes": 0,
        "symlink_entries": 0,
        "hardlink_duplicates": 0,
    }


def _tree_usage(path, *, max_entries=DEFAULT_MAX_ENTRIES):
    """Measure one fixed tree without following symlinks.

    count includes the root inode when present. Hard-linked inodes are counted
    once for allocated-byte attribution. Any read failure or entry bound hit
    makes scan_complete false rather than silently reporting a complete
    zero/partial result.
    """
    path = Path(path)
    result = _base_result()
    try:
        root_st = os.lstat(path)
    except FileNotFoundError:
        return result
    except OSError:
        result["scan_complete"] = False
        return result

    result["present"] = True
    seen = set()

    def account(st):
        key = (int(st.st_dev), int(st.st_ino))
        result["count"] += 1
        if key in seen:
            result["hardlink_duplicates"] += 1
            return
        seen.add(key)
        result["allocated_bytes"] += _allocated_bytes(st)

    account(root_st)
    if stat.S_ISLNK(root_st.st_mode):
        result["symlink_entries"] += 1
        result["scan_complete"] = False
        return result
    if not stat.S_ISDIR(root_st.st_mode):
        if not stat.S_ISREG(root_st.st_mode):
            result["scan_complete"] = False
        return result

    stack = [path]
    while stack:
        current = stack.pop()
        try:
            entries = os.scandir(current)
        except OSError:
            result["scan_complete"] = False
            continue
        try:
            with entries:
                for entry in entries:
                    if result["count"] >= max_entries:
                        result["scan_complete"] = False
                        return result
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    except OSError:
                        result["scan_complete"] = False
                        continue
                    account(st)
                    if stat.S_ISLNK(st.st_mode):
                        result["symlink_entries"] += 1
                        continue
                    if stat.S_ISDIR(st.st_mode):
                        stack.append(Path(entry.path))
        except OSError:
            result["scan_complete"] = False
    return result


def _file_usage(path):
    """Measure one fixed file path without following a symlink."""
    path = Path(path)
    result = _base_result()
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return result
    except OSError:
        result["scan_complete"] = False
        return result

    result["present"] = True
    result["count"] = 1
    result["allocated_bytes"] = _allocated_bytes(st)
    if stat.S_ISLNK(st.st_mode):
        result["symlink_entries"] = 1
        result["scan_complete"] = False
    elif not stat.S_ISREG(st.st_mode):
        result["scan_complete"] = False
    return result


def _db_family(db_path):
    db_path = Path(db_path)
    entries = {
        "db": _file_usage(db_path),
        "wal": _file_usage(Path(str(db_path) + "-wal")),
        "shm": _file_usage(Path(str(db_path) + "-shm")),
    }
    return {
        "scan_complete": all(item["scan_complete"] for item in entries.values()),
        "present_count": sum(1 for item in entries.values() if item["present"]),
        "allocated_bytes": sum(item["allocated_bytes"] for item in entries.values()),
        **entries,
    }


def collect_storage_breakdown(
    soren,
    prod_root,
    *,
    home_root=None,
    voicevox_root=Path("/opt/voicevox"),
    max_entries=DEFAULT_MAX_ENTRIES,
):
    """Return fixed storage categories for production root-cause attribution.

    Categories intentionally overlap: for example soren_tmp contains say_queue
    and the worker OpenCode DB. Consumers must compare categories but never add
    them together as if they were disjoint.
    """
    soren = Path(soren)
    prod_root = Path(prod_root)
    home = Path(home_root) if home_root is not None else soren.parent
    voicevox_root = Path(voicevox_root)

    default_db = home / ".local" / "share" / "opencode" / "opencode.db"
    worker_db = soren / "tmp" / "state" / "xdg_data" / "opencode" / "opencode.db"

    return {
        "version": 1,
        "categories_overlap": True,
        "max_entries_per_tree": int(max_entries),
        "opencode_default": _db_family(default_db),
        "opencode_worker": _db_family(worker_db),
        "opencode_default_total": _tree_usage(default_db.parent, max_entries=max_entries),
        "opencode_worker_total": _tree_usage(worker_db.parent, max_entries=max_entries),
        "soren_logs": _tree_usage(soren / "logs", max_entries=max_entries),
        "soren_tmp": _tree_usage(soren / "tmp", max_entries=max_entries),
        "say_queue": _tree_usage(soren / "tmp" / ".say_queue", max_entries=max_entries),
        "browser_profile": _tree_usage(
            soren / "tmp" / "soviet_local_chromium_profile", max_entries=max_entries
        ),
        "strategy_archive": _tree_usage(
            soren / "strategy_versions_archive" / "by_hash", max_entries=max_entries
        ),
        "docich_git": _tree_usage(prod_root / ".git", max_entries=max_entries),
        "soren_live_git": _tree_usage(soren / ".git", max_entries=max_entries),
        "soren_persist_git": _tree_usage(
            home / "soren-persist" / ".git", max_entries=max_entries
        ),
        "voicevox_root": _tree_usage(voicevox_root, max_entries=max_entries),
        "voicevox_archive": _file_usage(voicevox_root / "voicevox.7z.001"),
    }
