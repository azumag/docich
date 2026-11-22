"""pytest plugin: run one file-level shard of the collected tests.

Usage (CI):

    CI_SHARD=1/2 PYTHONPATH=.github/scripts python3 -m pytest -p ci_shard tests/ ...

Without ``CI_SHARD`` the plugin does nothing, so a local run is unchanged.

The shards are cut from what pytest actually collected, not from a hand-kept list of
files, so every collected test belongs to exactly one shard and a new test file needs
no CI edit. Items of one file always stay together, which keeps ``--dist loadfile``
meaningful inside a shard. The cut is deterministic: every pytest-xdist worker collects
the same items and must reach the same partition, or xdist aborts the run.

Files are packed largest first onto the lightest shard (longest-processing-time rule),
weighted by the number of collected tests. Ties go to the lowest shard.
"""
from __future__ import annotations

import os
from collections import Counter

ENV = 'CI_SHARD'


def parse_shard(spec: str) -> tuple[int, int]:
    """Return ``(index, count)`` for ``"index/count"`` with ``1 <= index <= count``."""
    try:
        index_text, count_text = spec.split('/')
        index, count = int(index_text), int(count_text)
    except ValueError:
        raise ValueError(f'{ENV} must look like "1/2", got {spec!r}') from None
    if not 1 <= index <= count:
        raise ValueError(f'{ENV} index must be within 1..{count}, got {spec!r}')
    return index, count


def assign(weights: dict[str, int], count: int) -> dict[str, int]:
    """Map every file to a 1-based shard so that the shard weights are close."""
    load = [0] * count
    owner: dict[str, int] = {}
    for path in sorted(weights, key=lambda name: (-weights[name], name)):
        lightest = min(range(count), key=lambda shard: (load[shard], shard))
        owner[path] = lightest + 1
        load[lightest] += weights[path]
    return owner


def file_of(nodeid: str) -> str:
    return nodeid.split('::', 1)[0]


def pytest_collection_modifyitems(config, items):
    spec = os.environ.get(ENV)
    if not spec:
        return
    index, count = parse_shard(spec)
    owner = assign(dict(Counter(file_of(item.nodeid) for item in items)), count)
    kept = [item for item in items if owner[file_of(item.nodeid)] == index]
    dropped = [item for item in items if owner[file_of(item.nodeid)] != index]
    if dropped:
        config.hook.pytest_deselected(items=dropped)
    items[:] = kept
