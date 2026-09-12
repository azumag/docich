#!/usr/bin/env python3
"""Render fixed-category worker state from sanitized VM diagnostics.

This runs on the GitHub Actions runner after the owner-only VM diagnostics
collector. Dynamic worker names never enter stdout: registered workers are
collapsed to the fixed categories in runtime_registry, and unregistered
entries are reduced to liveness/stale/pause counts only.
"""
import importlib.util
import json
import sys
from pathlib import Path


def _load_registry():
    path = Path(__file__).with_name("runtime_registry.py")
    spec = importlib.util.spec_from_file_location("vm_runtime_registry", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


WORKERS = _load_registry().WORKERS

WORKER_CATEGORIES = (
    "loop",
    "improvement",
    "chat",
    "audio",
    "monitor",
    "radio",
    "prediction",
    "overlay",
    "stream",
    "other",
)

CATEGORY_BY_NAME = {name: category for name, _required, category, _pid, _kind in WORKERS}


def _flag(record, name):
    return isinstance(record, dict) and record.get(name) is True


def summarize_worker_state(data):
    workers = data.get("workers") if isinstance(data, dict) else None
    workers = workers if isinstance(workers, dict) else {}
    details = workers.get("details")
    details = details if isinstance(details, dict) else {}

    paused = {category: 0 for category in WORKER_CATEGORIES}
    stale = {category: 0 for category in WORKER_CATEGORIES}
    for worker_name, category in CATEGORY_BY_NAME.items():
        record = details.get(worker_name)
        bucket = category if category in paused else "other"
        if _flag(record, "paused"):
            paused[bucket] += 1
        if _flag(record, "stale_pid_file"):
            stale[bucket] += 1

    unregistered_names = workers.get("unregistered")
    if not isinstance(unregistered_names, list):
        unregistered_names = []
    unregistered_alive = 0
    unregistered_stale = 0
    unregistered_paused = 0
    for name in unregistered_names:
        if not isinstance(name, str):
            continue
        record = details.get(name)
        if _flag(record, "alive"):
            unregistered_alive += 1
        if _flag(record, "stale_pid_file"):
            unregistered_stale += 1
        if _flag(record, "paused"):
            unregistered_paused += 1

    parts = []
    parts.extend(f"paused_{category}={paused[category]}" for category in WORKER_CATEGORIES)
    parts.extend(f"stale_pid_{category}={stale[category]}" for category in WORKER_CATEGORIES)
    parts.extend(
        [
            f"unregistered_alive={unregistered_alive}",
            f"unregistered_stale={unregistered_stale}",
            f"unregistered_paused={unregistered_paused}",
        ]
    )
    return ",".join(parts)


def main(argv):
    if len(argv) != 2:
        print("usage: summarize_worker_state.py <runtime-diagnostics.json>", file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as handle:
        wrapper = json.load(handle)
    if wrapper.get("status") != "diagnosed" or not isinstance(wrapper.get("diagnostics"), dict):
        raise SystemExit("invalid diagnostics envelope")
    print(f"worker_context={summarize_worker_state(wrapper['diagnostics'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
