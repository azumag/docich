#!/usr/bin/env python3
"""Render fixed-category storage metrics from sanitized production diagnostics."""
import json
import sys

DB_FAMILIES = ("opencode_default", "opencode_worker")
TREE_CATEGORIES = (
    "opencode_default_total",
    "opencode_worker_total",
    "soren_logs",
    "soren_tmp",
    "say_queue",
    "browser_profile",
    "strategy_archive",
    "docich_git",
    "soren_live_git",
    "soren_persist_git",
    "voicevox_root",
    "voicevox_archive",
)


def _uint(mapping, key):
    value = mapping.get(key) if isinstance(mapping, dict) else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def render(data):
    if not isinstance(data, dict):
        return 0, 1, "storage_breakdown_unavailable=1"
    breakdown = data.get("storage_breakdown")
    if not isinstance(breakdown, dict) or breakdown.get("version") != 1:
        return 0, 1, "storage_breakdown_unavailable=1"

    parts = ["overlap=1"]
    incomplete = False
    for family in DB_FAMILIES:
        item = breakdown.get(family)
        complete = isinstance(item, dict) and item.get("scan_complete") is True
        incomplete = incomplete or not complete
        parts.append(f"{family}_complete={int(complete)}")
        for suffix in ("db", "wal", "shm"):
            leaf = item.get(suffix) if isinstance(item, dict) else None
            leaf_complete = isinstance(leaf, dict) and leaf.get("scan_complete") is True
            incomplete = incomplete or not leaf_complete
            parts.append(f"{family}_{suffix}_bytes={_uint(leaf, 'allocated_bytes')}")
        parts.append(f"{family}_total_bytes={_uint(item, 'allocated_bytes')}")

    for category in TREE_CATEGORIES:
        item = breakdown.get(category)
        complete = isinstance(item, dict) and item.get("scan_complete") is True
        incomplete = incomplete or not complete
        parts.append(f"{category}_complete={int(complete)}")
        parts.append(f"{category}_bytes={_uint(item, 'allocated_bytes')}")
        parts.append(f"{category}_count={_uint(item, 'count')}")

    return 1, int(incomplete), ",".join(parts)


def main(argv):
    if len(argv) != 2:
        print("usage: summarize_storage.py <runtime-diagnostics.json>", file=sys.stderr)
        return 2
    try:
        with open(argv[1], encoding="utf-8") as handle:
            wrapper = json.load(handle)
    except (OSError, ValueError):
        print("storage_breakdown_available=0")
        print("storage_breakdown_incomplete=1")
        print("storage_context=storage_breakdown_unavailable=1")
        return 0

    diagnostics = wrapper.get("diagnostics") if isinstance(wrapper, dict) else None
    available, incomplete, context = render(diagnostics)
    print(f"storage_breakdown_available={available}")
    print(f"storage_breakdown_incomplete={incomplete}")
    print(f"storage_context={context}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
